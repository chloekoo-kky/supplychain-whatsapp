# warehouse/models.py
from django.db import models, transaction
from django.db.models import Sum, Max, F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify # For generating default codes if needed

class Warehouse(models.Model):
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.name

class WarehouseProduct(models.Model):
    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.CASCADE)
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)
    # New 'code' field
    code = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Easy to remember, human-readable code for this product in this warehouse (e.g., WH1-PROD-001)."
    )
    quantity = models.IntegerField(default=0)
    threshold = models.IntegerField(default=0)
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE, null=True, blank=True) # Made supplier blankable too

    class Meta:
        unique_together = [
            ('warehouse', 'product'), # Existing unique constraint
            ('warehouse', 'code')     # New: Code must be unique within a warehouse if not null
        ]
        ordering = ['warehouse__name', 'product__name']


    def __str__(self):
        # Include code in string representation if it exists
        if self.code:
            return f"{self.code} - {self.product.sku} - {self.product.name} @ {self.warehouse.name}"
        return f"{self.product.sku} - {self.product.name} @ {self.warehouse.name}"

    def save(self, *args, **kwargs):
        if not self.code and self.product and self.warehouse: # Auto-generate a simple code if empty
            # Example auto-generation: WH_ID-PROD_ID-SKU_SUFFIX
            # You might want a more sophisticated or sequential generator
            # For now, let's make it potentially based on SKU and warehouse name part
            # Ensure it's unique within the warehouse if uniqueness is enforced by DB
            # temp_code = f"{slugify(self.warehouse.name)[:5].upper()}-{self.product.sku}"
            # To avoid issues with uniqueness constraint during initial save if code is required,
            # it's often better to allow it to be blank/null initially and populate later or via admin.
            # For this iteration, we'll keep it blank=True, null=True.
            # If you make it non-nullable, you MUST provide a default or generate it here robustly.
            pass # Keep it blank if not provided, user can fill in admin

        # Ensure code is None if it's an empty string to work with unique_together constraint
        # if self.code == "":
        #     self.code = None

        super().save(*args, **kwargs)


    def is_below_threshold(self):
        return self.quantity < self.threshold

    @property
    def pending_arrival(self):
        incoming = PurchaseOrderItem.objects.select_related('purchase_order').filter(
            item=self,
            purchase_order__status__in=['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED']
        ).exclude(purchase_order__status='DELIVERED').aggregate(total_pending=Sum('quantity'))

        already_received = PurchaseOrderItem.objects.filter(
            item=self,
            purchase_order__status__in=['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED']
        ).aggregate(total_received=Sum('received_quantity'))

        pending_qty = (incoming['total_pending'] or 0) - (already_received['total_received'] or 0)
        return max(0, pending_qty)


    @property
    def incoming_po_items(self):
        return PurchaseOrderItem.objects.select_related('purchase_order__supplier', 'item__product').filter(
            item=self,
            purchase_order__status__in=['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED']
        ).exclude(purchase_order__status='DELIVERED').order_by('purchase_order__eta', 'purchase_order_id')

    @property
    def total_quantity(self):
        aggregation = self.batches.aggregate(total=Sum('quantity'))
        return aggregation['total'] or 0

# ... (PurchaseOrder, PurchaseOrderItem, etc. models remain the same) ...
# Ensure PurchaseOrderItem is defined or imported if used by pending_arrival
class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('WAITING_INVOICE', 'Waiting for Invoice'),
        ('PAYMENT_MADE', 'Payment Made'),
        ('PARTIALLY_DELIVERED', 'Partially Delivered'),
        ('DELIVERED', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    ]
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    notes = models.TextField(blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)
    eta = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    draft_date = models.DateTimeField(null=True, blank=True)
    waiting_invoice_date = models.DateTimeField(null=True, blank=True)
    payment_made_date = models.DateTimeField(null=True, blank=True)
    partially_delivered_date = models.DateTimeField(null=True, blank=True)
    delivered_date = models.DateTimeField(null=True, blank=True)
    cancelled_date = models.DateTimeField(null=True, blank=True)

    def set_status_date(self, status_code):
        field_map = {
            'DRAFT': 'draft_date',
            'WAITING_INVOICE': 'waiting_invoice_date',
            'PAYMENT_MADE': 'payment_made_date',
            'PARTIALLY_DELIVERED': 'partially_delivered_date',
            'DELIVERED': 'delivered_date',
            'CANCELLED': 'cancelled_date',
        }
        ordered_statuses = ['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED', 'DELIVERED'] # Exclude CANCELLED from auto-backfill logic path

        try:
            target_index = ordered_statuses.index(status_code)
            for i in range(target_index + 1):
                code_to_set = ordered_statuses[i]
                field_name = field_map.get(code_to_set)
                if field_name and getattr(self, field_name) is None:
                    setattr(self, field_name, timezone.now())
        except ValueError: # Handle cases like 'CANCELLED' not in ordered_statuses for backfill
            field_name = field_map.get(status_code)
            if field_name and getattr(self, field_name) is None:
                 setattr(self, field_name, timezone.now())

        # Specifically for PARTIALLY_DELIVERED, always update its timestamp if it's the current status
        if status_code == 'PARTIALLY_DELIVERED':
            self.partially_delivered_date = timezone.now()


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

    def update_received_quantities_and_status(self):
        if not self.items.exists():
            if self.status not in ['DRAFT', 'CANCELLED']:
                self.status = 'DRAFT'
                self.save()
            return

        all_items_fully_received = not self.items.filter(received_quantity__lt=F('quantity')).exists()
        any_item_received_at_all = self.items.filter(received_quantity__gt=0).exists()
        new_status = self.status

        if self.status == 'CANCELLED':
            pass
        elif all_items_fully_received:
            new_status = 'DELIVERED'
        elif any_item_received_at_all:
            new_status = 'PARTIALLY_DELIVERED'
        else:
            if self.status in ['PARTIALLY_DELIVERED', 'DELIVERED']:
                new_status = 'PAYMENT_MADE'
        if new_status != self.status:
            self.status = new_status
            self.save()

    def is_fully_received(self):
        if not self.items.exists():
            return False
        return not self.items.filter(received_quantity__lt=models.F('quantity')).exists()

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        old_status = None
        if not is_new:
            try:
                old_status = PurchaseOrder.objects.get(pk=self.pk).status
            except PurchaseOrder.DoesNotExist:
                pass
        if is_new and self.status == 'DRAFT' and not self.draft_date:
            self.draft_date = timezone.now()
        if old_status != self.status:
            self.set_status_date(self.status)
        super().save(*args, **kwargs)

class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(WarehouseProduct, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    received_quantity = models.PositiveIntegerField(default=0)

    @property
    def total_price(self):
        return self.quantity * self.price

    @property
    def balance_quantity(self):
        return self.quantity - self.received_quantity

    def __str__(self):
        return f"{self.item.product.name} x {self.quantity} (Received: {self.received_quantity}) for PO#{self.purchase_order.id}"

class PurchaseOrderReceiptLog(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='receipt_logs')
    receipt_date = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True, null=True)
    def __str__(self):
        return f"Receipt for PO#{self.purchase_order.id} on {self.receipt_date.strftime('%Y-%m-%d %H:%M')}"

class PurchaseOrderReceiptItem(models.Model):
    receipt_log = models.ForeignKey(PurchaseOrderReceiptLog, on_delete=models.CASCADE, related_name='received_items')
    po_item = models.ForeignKey(PurchaseOrderItem, on_delete=models.CASCADE, related_name='receipt_entries')
    quantity_received_this_time = models.PositiveIntegerField()
    def __str__(self):
        return f"{self.quantity_received_this_time} of {self.po_item.item.product.name} for PO#{self.receipt_log.purchase_order.id}"
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

