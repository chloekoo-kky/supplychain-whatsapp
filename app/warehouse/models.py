# ===== warehouse/models.py =====
from django.db import models, transaction
from django.db.models import Max, F, Value
from django.db.models.functions import Coalesce

from django.utils import timezone

from inventory.models import StockTransaction

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
        from django.db.models import Sum
        from .models import PurchaseOrderItem
        incoming = PurchaseOrderItem.objects.select_related('purchase_order').filter(
            item=self,
            purchase_order__status__in=['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE']
        ).aggregate(total=Sum('quantity'))['total'] or 0
        return incoming

    @property
    def incoming_po_items(self):
        from .models import PurchaseOrderItem
        return PurchaseOrderItem.objects.select_related('purchase_order').filter(
            item=self,
            purchase_order__status__in=['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE']
        )


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
    inventory_updated = models.BooleanField(default=False)


    # 状态时间戳
    draft_date = models.DateTimeField(null=True, blank=True)
    waiting_invoice_date = models.DateTimeField(null=True, blank=True)
    payment_made_date = models.DateTimeField(null=True, blank=True)
    partially_delivered_date = models.DateTimeField(null=True, blank=True)
    delivered_date = models.DateTimeField(null=True, blank=True)
    cancelled_date = models.DateTimeField(null=True, blank=True)

    def apply_inventory_movement(self, quantity_factor=1):
    # Apply inventory movement based on PO items.
    # quantity_factor:
    # +1 ➝ delivery(DELIVERED)
    # -1 ➝ cancellation (CANCELLED)"""
        movement_type = 'IN' if quantity_factor > 0 else 'CANCEL'

        with transaction.atomic():
            for item in self.items.select_related('item__warehouse', 'item__product'):
                wp = item.item  # WarehouseProduct
                qty_change = item.quantity * quantity_factor

                # ✅ 更新仓库库存
                wp.quantity += qty_change
                wp.save()

                # ✅ 创建库存记录
                StockTransaction.objects.create(
                    warehouse=wp.warehouse,
                    warehouse_product=wp,
                    product=wp.product,
                    transaction_type=movement_type,
                    quantity=qty_change,
                    reference_note=f"PO #{self.id}",
                    related_po=self,
                )

    def save(self, *args, **kwargs):
        is_new = self.pk is None  # 用于区分首次创建 vs 更新

        # ✅ 新建时，如是 DRAFT 且未设定 draft_date，则自动设定
        if is_new and self.status == 'DRAFT' and self.draft_date is None:
            self.draft_date = timezone.now()

        self.last_updated_date = timezone.now()

        # 先保存主对象
        super().save(*args, **kwargs)

        # ✅ 处理状态变更逻辑（只在已存在对象更新时才触发）
        if not is_new:
            previous = PurchaseOrder.objects.get(pk=self.pk)

            if previous.status != self.status:
                if previous.status != 'DELIVERED' and self.status == 'DELIVERED':
                    self.apply_inventory_movement(quantity_factor=1)
                    self.inventory_updated = True
                    super().save(update_fields=['inventory_updated'])

                elif previous.status != 'CANCELLED' and self.status == 'CANCELLED':
                    self.apply_inventory_movement(quantity_factor=-1)
                    self.inventory_updated = True
                    super().save(update_fields=['inventory_updated'])




    def set_status_date(self, status):
        """根据状态设置对应时间字段（只设置一次）"""
        field_map = {
            'DRAFT': 'draft_date',
            'WAITING_INVOICE': 'waiting_invoice_date',
            'PAYMENT_MADE': 'payment_made_date',
            'PARTIALLY_DELIVERED': 'partially_delivered_date',
            'DELIVERED': 'delivered_date',
            'CANCELLED': 'cancelled_date',
        }
        field = field_map.get(status)
        if field and getattr(self, field) is None:
            setattr(self, field, timezone.now())

    def __str__(self):
        return f"PO#{self.id} - {self.supplier.name} - {self.status}"


    @property
    def total_amount(self):
        return sum(item.total_price for item in self.items.all())


    @property
    def status_updated_date(self):
        return max(filter(None, [
            self.draft_date,
            self.waiting_invoice_date,
            self.payment_made_date,
            self.partially_delivered_date,
            self.delivered_date,
            self.cancelled_date,
        ]), default=self.draft_date)

class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(WarehouseProduct, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def total_price(self):
        return self.quantity * self.price
