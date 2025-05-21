# app/operation/models.py
from django.db import models
from django.conf import settings # For settings.AUTH_USER_MODEL
from django.utils import timezone
from django.db import transaction
import uuid # For generating unique codes
import random
import string


# Assuming these models are correctly defined in their respective apps
from inventory.models import Product, InventoryBatchItem
from warehouse.models import Warehouse, WarehouseProduct

def generate_parcel_code(warehouse_name=None):
    """
    Generates a unique parcel code.
    If warehouse_name is provided, uses its first 3 letters as prefix.
    Otherwise, uses "XXX-" or a generic prefix.
    Format: WHS-4B2A
    """
    if warehouse_name and len(warehouse_name) >= 3:
        prefix = warehouse_name[:3].upper() + "-"
    else:
        prefix = "GEN-" # Generic prefix if warehouse name is too short or not provided

    hex_chars = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    random_part = ''.join(random.choices(hex_chars, k=4))
    return f"{prefix}{random_part}"


class Order(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PENDING_IMPORT', 'Pending Import Data'),
        ('PENDING_ALLOCATION', 'Pending Allocation'),
        ('PARTIALLY_ALLOCATED', 'Partially Allocated'),
        ('FULLY_ALLOCATED', 'Fully Allocated'),
        ('PENDING_SHIPMENT', 'Pending Shipment'),
        ('PARTIALLY_SHIPPED', 'Partially Shipped'),
        ('SHIPPED', 'Shipped'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('IMPORT_FAILED', 'Import Failed'),
    ]

    erp_order_id = models.CharField(
        max_length=50,
        unique=True,
        help_text="Order ID from the ERP system or uploaded file."
    )
    order_date = models.DateField(
        help_text="Date the order was placed, from the ERP file."
    )
    warehouse = models.ForeignKey(
        'warehouse.Warehouse',
        on_delete=models.SET_NULL,
        null=True,
        help_text="Warehouse associated with this order."
    )
    customer_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Customer's name (Address name from file)."
    )
    company_name = models.CharField(
        max_length=255,
        blank=True, null=True,
        help_text="Customer's company name (from file)."
    )
    recipient_address_line1 = models.CharField(max_length=255, blank=True, help_text="Street address from file.")
    recipient_address_city = models.CharField(max_length=100, blank=True)
    recipient_address_state = models.CharField(max_length=100, blank=True)
    recipient_address_zip = models.CharField(max_length=20, blank=True)
    recipient_address_country = models.CharField(max_length=100, blank=True)
    recipient_phone = models.CharField(max_length=30, blank=True, null=True)
    vat_number = models.CharField(max_length=50, blank=True, null=True)

    title_notes = models.TextField(blank=True, null=True, help_text="Title/General notes from the file.")
    shipping_notes = models.TextField(blank=True, null=True, help_text="Shipping specific notes from the file.")

    # System Generated Fields
    parcel_code = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        help_text="Auto-generated unique code for this order/parcel group."
    )
    status = models.CharField(
        max_length=25,
        choices=STATUS_CHOICES,
        default='PENDING_IMPORT',
        help_text="Current status of the order processing."
    )
    is_cold_chain = models.BooleanField(
        default=False,
        help_text="True if any item in the order requires cold chain shipping."
    )

    courier_name = models.CharField(max_length=100, blank=True, null=True)
    tracking_number = models.CharField(max_length=100, blank=True, null=True)

    imported_at = models.DateTimeField(auto_now_add=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='imported_orders'
    )
    last_updated_at = models.DateTimeField(auto_now=True)
    processing_log = models.TextField(blank=True, null=True, help_text="Log of import/processing steps or errors.")

    class Meta:
        ordering = ['-order_date', '-imported_at']
        verbose_name = "Customer Order"
        verbose_name_plural = "Customer Orders"

    def __str__(self):
        return f"Order {self.erp_order_id} ({self.parcel_code}) - {self.customer_name}"

    def get_full_address(self):
        parts = [
            self.recipient_address_line1,
            self.recipient_address_city,
            self.recipient_address_state,
            self.recipient_address_zip,
            self.recipient_address_country
        ]
        return ", ".join(filter(None, parts))

    def update_cold_chain_status(self):
        if self.items.filter(is_cold_item=True).exists():
            self.is_cold_chain = True
        else:
            self.is_cold_chain = False

    def save(self, *args, **kwargs):
        if not self.parcel_code and self.warehouse:
            # Generate a unique parcel code
            while True:
                new_code = generate_parcel_code(self.warehouse.name)
                if not Order.objects.filter(parcel_code=new_code).exists():
                    self.parcel_code = new_code
                    break
        elif not self.parcel_code: # Fallback if warehouse not set yet
             while True:
                new_code = generate_parcel_code("DEF") # Default prefix
                if not Order.objects.filter(parcel_code=new_code).exists():
                    self.parcel_code = new_code
                    break
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    ITEM_STATUS_CHOICES = [
        ('PENDING_ALLOCATION', 'Pending Allocation'),
        ('ALLOCATED', 'Stock Allocated'),
        ('ALLOCATION_FAILED', 'Allocation Failed'),
        ('READY_TO_SHIP', 'Ready to Ship'),
        ('SHIPPED', 'Shipped'),
        ('CANCELLED', 'Cancelled'),
    ]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, help_text="The generic product.")
    warehouse_product = models.ForeignKey(
        WarehouseProduct,
        on_delete=models.PROTECT,
        null=True, blank=True,
        help_text="The specific warehouse stock item for this product."
    )
    erp_product_name = models.CharField(max_length=255, blank=True, help_text="Product name as it appeared in the ERP/uploaded file.")
    quantity_ordered = models.PositiveIntegerField()
    quantity_allocated = models.PositiveIntegerField(default=0)
    quantity_shipped = models.PositiveIntegerField(default=0)
    suggested_batch_item = models.ForeignKey(
        InventoryBatchItem,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='allocated_order_items',
        help_text="The specific batch suggested or allocated for this order item."
    )
    suggested_batch_number_display = models.CharField(max_length=100, blank=True, null=True)
    suggested_batch_expiry_date_display = models.DateField(null=True, blank=True)
    is_cold_item = models.BooleanField(default=False, help_text="Does this specific item require cold chain handling (from ERP file).")
    status = models.CharField(max_length=25, choices=ITEM_STATUS_CHOICES, default='PENDING_ALLOCATION')
    notes = models.TextField(blank=True, null=True, help_text="Notes specific to this order item processing.")

    class Meta:
        ordering = ['order', 'product__name']
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"

    def __str__(self):
        return f"{self.quantity_ordered} x {self.product.name if self.product else self.erp_product_name} (Order: {self.order.erp_order_id})"

    @property
    def quantity_remaining_to_ship(self):
        return self.quantity_ordered - self.quantity_shipped




class Parcel(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='parcels')
    parcel_code_system = models.CharField(max_length=50, blank=True, null=True, help_text="System's internal or user-defined code for this specific parcel, if different from main order parcel code.")
    courier_name = models.CharField(max_length=100, blank=True, null=True)
    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Shipment Parcel"
        verbose_name_plural = "Shipment Parcels"

    def __str__(self):
        return f"Parcel for Order {self.order.erp_order_id} - {self.tracking_number or 'No Tracking'}"


class ParcelItem(models.Model):
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name='items_in_parcel')
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name='shipments')
    shipped_from_batch = models.ForeignKey(
        InventoryBatchItem,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text="The specific inventory batch this parcel item was shipped from."
    )
    quantity_shipped_in_this_parcel = models.PositiveIntegerField()

    class Meta:
        ordering = ['parcel', 'order_item']
        verbose_name = "Parcel Item"
        verbose_name_plural = "Parcel Items"
        unique_together = [['parcel', 'order_item', 'shipped_from_batch']]

    def __str__(self):
        return f"{self.quantity_shipped_in_this_parcel} x {self.order_item.product.name if self.order_item.product else self.order_item.erp_product_name} in Parcel for Order {self.parcel.order.erp_order_id}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # TODO: Add logic to update OrderItem.quantity_shipped
        # TODO: Add logic to decrement InventoryBatchItem.quantity and create StockTransaction

    @transaction.atomic
    def delete(self, *args, **kwargs):
        # TODO: Add logic to revert OrderItem.quantity_shipped
        # TODO: Add logic to increment InventoryBatchItem.quantity and create reverse StockTransaction
        super().delete(*args, **kwargs)

