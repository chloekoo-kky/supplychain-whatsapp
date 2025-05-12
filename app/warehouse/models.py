# ===== warehouse/models.py =====
from django.db import models, transaction
from django.db.models import Sum, Max, F, Value
from django.db.models.functions import Coalesce


from django.utils import timezone


class Warehouse(models.Model):
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.name

class WarehouseProduct(models.Model):
    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.CASCADE)
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    threshold = models.IntegerField(default=0)
    batch_number = models.CharField(max_length=100, blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)  # ⬅️ 新增
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE, null=True)

    class Meta:
        unique_together = ('warehouse', 'product', 'batch_number')  # ✅ 一个仓库一个产品的一个批次唯一

    def __str__(self):
        return f"{self.product.sku} - {self.product.name} @ {self.warehouse.name} (Batch {self.batch_number or 'N/A'})"

    def is_below_threshold(self):
        return self.quantity < self.threshold

    @property
    def pending_arrival(self):
        # Ensure PurchaseOrderItem is imported or defined before this point if not already
        incoming = PurchaseOrderItem.objects.select_related('purchase_order').filter(
            item=self,
            purchase_order__status__in=['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED'] # Added PARTIALLY_DELIVERED
        ).exclude(purchase_order__status='DELIVERED').aggregate(total_pending=Sum('quantity'))

        already_received = PurchaseOrderItem.objects.filter(item=self, purchase_order__status__in=['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED']).aggregate(total_received=Sum('received_quantity'))

        pending_qty = (incoming['total_pending'] or 0) - (already_received['total_received'] or 0)
        return max(0, pending_qty)


    @property
    def incoming_po_items(self):
        # Ensure PurchaseOrderItem is imported or defined
        return PurchaseOrderItem.objects.select_related('purchase_order__supplier', 'item__product').filter(
            item=self,
            purchase_order__status__in=['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED']
        ).exclude(purchase_order__status='DELIVERED').order_by('purchase_order__eta', 'purchase_order_id')


class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('WAITING_INVOICE', 'Waiting for Invoice'),
        ('PAYMENT_MADE', 'Payment Made'),
        ('PARTIALLY_DELIVERED', 'Partially Delivered'),  # 支持部分交货
        ('DELIVERED', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    ]

    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    notes = models.TextField(blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)
    eta = models.DateField(null=True, blank=True)

    # Status Timestamps
    draft_date = models.DateTimeField(null=True, blank=True)
    waiting_invoice_date = models.DateTimeField(null=True, blank=True)
    payment_made_date = models.DateTimeField(null=True, blank=True)
    partially_delivered_date = models.DateTimeField(null=True, blank=True) # Stores the LATEST partial delivery date
    delivered_date = models.DateTimeField(null=True, blank=True)
    cancelled_date = models.DateTimeField(null=True, blank=True)

    def set_status_date(self, status_code):
        """Sets the appropriate date field when a status is achieved, and backfills skipped dates."""
        field_map = {
            'DRAFT': 'draft_date',
            'WAITING_INVOICE': 'waiting_invoice_date',
            'PAYMENT_MADE': 'payment_made_date',
            'PARTIALLY_DELIVERED': 'partially_delivered_date',
            'DELIVERED': 'delivered_date',
            'CANCELLED': 'cancelled_date',
        }
        ordered_statuses = ['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED', 'DELIVERED', 'CANCELLED']

        target_index = ordered_statuses.index(status_code)

        for i in range(target_index + 1):
            code = ordered_statuses[i]
            field_name = field_map.get(code)
            if field_name and getattr(self, field_name) is None:
                # If the field is None (not set), set it to now
                setattr(self, field_name, timezone.now())
        if status_code == 'PARTIALLY_DELIVERED':
            setattr(self, field_name, timezone.now())
        elif getattr(self, field_name) is None :
            setattr(self, field_name, timezone.now())



    def __str__(self):
        return f"PO#{self.id} - {self.supplier.name} - {self.status}"

    @property
    def total_amount(self):
        return sum(item.total_price for item in self.items.all())

    @property
    def total_ordered_quantity(self):
        return self.items.aggregate(total=Sum('quantity'))['total'] or 0

    @property
    def total_received_quantity(self):
        return self.items.aggregate(total=Sum('received_quantity'))['total'] or 0

    def is_fully_received(self):
            """Checks if all items in the PO have been fully received."""
            if not self.items.exists():
                return False # Or True, depending on business logic for empty POs
            return not self.items.filter(received_quantity__lt=models.F('quantity')).exists()

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        old_status = None
        if not is_new:
            try:
                old_status = PurchaseOrder.objects.get(pk=self.pk).status
            except PurchaseOrder.DoesNotExist:
                pass # Should not happen if not is_new

        if is_new and self.status == 'DRAFT' and not self.draft_date:
            self.draft_date = timezone.now()

        # Update status date if status has changed
        if old_status != self.status:
            self.set_status_date(self.status)

        super().save(*args, **kwargs) # Save first to get PK for relations if new

    # @property
    # def status_updated_date(self):
    #     return max(filter(None, [
    #         self.draft_date,
    #         self.waiting_invoice_date,
    #         self.payment_made_date,
    #         self.partially_delivered_date,
    #         self.delivered_date,
    #         self.cancelled_date,
    #     ]), default=self.draft_date)

    # def apply_inventory_movement(self, quantity_factor=1):
    # # Apply inventory movement based on PO items.
    # # quantity_factor:
    # # +1 ➝ delivery(DELIVERED)
    # # -1 ➝ cancellation (CANCELLED)"""
    #     movement_type = 'IN' if quantity_factor > 0 else 'CANCEL'

    #     with transaction.atomic():
    #         for item in self.items.select_related('item__warehouse', 'item__product'):
    #             wp = item.item  # WarehouseProduct
    #             qty_change = item.quantity * quantity_factor

    #             # ✅ 更新仓库库存
    #             wp.quantity += qty_change
    #             wp.save()

    #             # ✅ 创建库存记录
    #             StockTransaction.objects.create(
    #                 warehouse=wp.warehouse,
    #                 warehouse_product=wp,
    #                 product=wp.product,
    #                 transaction_type=movement_type,
    #                 quantity=qty_change,
    #                 reference_note=f"PO #{self.id}",
    #                 related_po=self,
    #             )



class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(WarehouseProduct, on_delete=models.CASCADE) # This is WarehouseProduct
    quantity = models.PositiveIntegerField() # Ordered quantity
    price = models.DecimalField(max_digits=10, decimal_places=2)
    received_quantity = models.PositiveIntegerField(default=0) # Total quantity received for this item

    @property
    def total_price(self):
        return self.quantity * self.price

    @property
    def balance_quantity(self):
        return self.quantity - self.received_quantity

    def __str__(self):
        return f"{self.item.product.name} x {self.quantity} (Received: {self.received_quantity}) for PO#{self.purchase_order.id}"

class PurchaseOrderReceiptLog(models.Model):
    """Logs each instance of receiving goods against a Purchase Order."""
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='receipt_logs')
    receipt_date = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True, null=True)
    # user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True) # Optional: track who received

    def __str__(self):
        return f"Receipt for PO#{self.purchase_order.id} on {self.receipt_date.strftime('%Y-%m-%d %H:%M')}"

class PurchaseOrderReceiptItem(models.Model):
    """Details of items received in a specific receipt log."""
    receipt_log = models.ForeignKey(PurchaseOrderReceiptLog, on_delete=models.CASCADE, related_name='received_items')
    po_item = models.ForeignKey(PurchaseOrderItem, on_delete=models.CASCADE, related_name='receipt_entries')
    quantity_received_this_time = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity_received_this_time} of {self.po_item.item.product.name} for PO#{self.receipt_log.purchase_order.id}"

    def save(self, *args, **kwargs):
        # This model's save doesn't directly update WarehouseProduct or StockTransaction.
        # That logic is centralized in the `process_po_receipt` view.
        super().save(*args, **kwargs)
