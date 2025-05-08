from django.db import models
from django.conf import settings

from operation.models import Order


class Supplier(models.Model):
    name = models.CharField(max_length=100, null=True)
    code = models.CharField(max_length=20, unique=True, null=True)  # ⬅️ 新增 supplier code
    address = models.TextField(null=True)
    whatsapp_number = models.CharField(max_length=20, null=True)
    email = models.EmailField(blank=True, null=True)

    def __str__(self):
        return f"{self.name} - {self.address}"


class Product(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True)
    created_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name}"

    def increase_stock(self, amount):
        self.quantity += amount
        self.save()

    def decrease_stock(self, amount):
        if amount > self.quantity:
            raise ValueError("Short quantity, Order cannot be proceed.")
        self.quantity -= amount
        self.save()


class StockTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('IN', 'Stock In (PO Received)'),
        ('OUT', 'Stock Out (Sales Order)'),
        ('RETURN', 'Return In'),
        ('CANCEL', 'Cancel / Restore'),
        ('ADJUST', 'Manual Adjustment'),
    ]

    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.CASCADE)
    warehouse_product = models.ForeignKey('warehouse.WarehouseProduct', on_delete=models.CASCADE)
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)  # 冗余字段，加速聚合分析
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    quantity = models.IntegerField()  # 正数为加，负数为减
    reference_note = models.CharField(max_length=255, blank=True)  # 可填“PO #123”、“SO #456”
    related_po = models.ForeignKey('warehouse.PurchaseOrder', null=True, blank=True, on_delete=models.SET_NULL)
    related_order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_transaction_type_display()}] {self.product.name} x {self.quantity} @ {self.warehouse.name}"

