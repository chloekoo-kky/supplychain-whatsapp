# app/operation/models.py
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum
import uuid
import random
import string

from inventory.models import Product, InventoryBatchItem # Assuming direct import is fine
from warehouse.models import Warehouse, WarehouseProduct

def generate_parcel_code(warehouse_name=None, order_erp_id=None):
    prefix_parts = []
    if warehouse_name and len(warehouse_name) >= 3:
        prefix_parts.append(warehouse_name[:3].upper())
    else:
        prefix_parts.append("GEN")

    if order_erp_id:
        sanitized_erp_id = ''.join(filter(str.isalnum, str(order_erp_id)))[:6].upper()
        if sanitized_erp_id:
            prefix_parts.append(sanitized_erp_id)

    prefix = "-".join(prefix_parts) + "-" if prefix_parts else "PCL-"
    hex_chars = "123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
    random_part = ''.join(random.choices(hex_chars, k=4))
    return f"{prefix}{random_part}"

class Order(models.Model):
    STATUS_CHOICES = [
        ('NEW_ORDER', 'New'), # Changed from 'NEW ORDER' to 'NEW_ORDER' for consistency
        ('PARTIALLY_SHIPPED', 'Partial'),
        ('FULLY_SHIPPED', 'Shipped'),
        ('DELIVERED', 'Delivered'),
        ('DELIVERY_FAILED', 'Failed'), # Covers "returned by custom" logic
        ('RETURNED_COURIER', 'Returned by Courier'), # Items to be restocked
        ('ADJUSTED_TO_CLOSE', 'Adjusted to Close'),
        ('INVOICE_ISSUED', 'Billed (Completed)')
    ]

    erp_order_id = models.CharField(max_length=50, unique=True, help_text="Order ID from the ERP system or uploaded file.")
    order_date = models.DateField(help_text="Date the order was placed, from the ERP file.")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, help_text="Warehouse associated with this order.")
    customer_name = models.CharField(max_length=255, blank=True, help_text="Customer's name (Address name from file).")
    company_name = models.CharField(max_length=255, blank=True, null=True, help_text="Customer's company name (from file).")
    recipient_address_line1 = models.CharField(max_length=255, blank=True, help_text="Street address from file.")
    recipient_address_city = models.CharField(max_length=100, blank=True)
    recipient_address_state = models.CharField(max_length=100, blank=True)
    recipient_address_zip = models.CharField(max_length=20, blank=True)
    recipient_address_country = models.CharField(max_length=100, blank=True)
    recipient_phone = models.CharField(max_length=30, blank=True, null=True)
    vat_number = models.CharField(max_length=50, blank=True, null=True)
    title_notes = models.TextField(blank=True, null=True, help_text="Title/General notes from the file.")
    shipping_notes = models.TextField(blank=True, null=True, help_text="Shipping specific notes from the file.")
    order_display_code = models.CharField(max_length=50, unique=False, blank=True, help_text="Auto-generated unique display code for this order.")
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='NEW_ORDER', help_text="Current status of the order processing.")
    is_cold_chain = models.BooleanField(default=False, help_text="True if any item in the order requires cold chain shipping.")
    imported_at = models.DateTimeField(auto_now_add=True)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='imported_orders')
    last_updated_at = models.DateTimeField(auto_now=True)
    processing_log = models.TextField(blank=True, null=True, help_text="Log of import/processing steps or errors.")

    class Meta:
        ordering = ['-order_date', '-imported_at']
        verbose_name = "Customer Order"
        verbose_name_plural = "Customer Orders"

    def __str__(self):
        return f"Order {self.erp_order_id} ({self.order_display_code or 'No Code'}) - {self.get_status_display()}"

    def update_status_based_on_items_and_parcels(self):
        """
        Updates the order's overall status based on its items and parcels.
        This logic should be refined to match your exact workflow.
        """
        total_items = self.items.count()
        if total_items == 0 and self.status not in ['NEW_ORDER', 'CANCELLED', 'ADJUSTED_TO_CLOSE', 'INVOICE_ISSUED']: # Or other initial/final states
            self.status = 'NEW_ORDER' # Or an appropriate "empty" state
            return

        shipped_item_count = self.items.filter(status='ITEM_SHIPPED').count()
        delivered_item_count = self.items.filter(status='ITEM_DELIVERED').count()
        # Add counts for other relevant item statuses like ITEM_RETURNED_RESTOCKED, ITEM_DELIVERY_FAILED, CANCELLED

        # Example simplified logic:
        if self.parcels.filter(status='DELIVERED').count() == self.parcels.count() and self.parcels.exists():
             self.status = 'DELIVERED'
        elif self.parcels.filter(status='RETURNED_COURIER').exists():
            self.status = 'RETURNED_COURIER'
        elif self.parcels.filter(status='DELIVERY_FAILED').exists():
             self.status = 'DELIVERY_FAILED'
        elif shipped_item_count == total_items and total_items > 0:
            self.status = 'FULLY_SHIPPED'
        elif shipped_item_count > 0:
            self.status = 'PARTIALLY_SHIPPED'
        # ... more conditions for ADJUSTED_TO_CLOSE, INVOICE_ISSUED, NEW_ORDER
        # This is where your business logic for order status transitions based on item/parcel events lives.
        # For instance, if all items are 'PENDING_FULFILLMENT' and the order was just created, it's 'NEW_ORDER'.
        # If some are shipped and some are cancelled -> 'ADJUSTED_TO_CLOSE'
        # If all parcels are 'BILLED' -> 'INVOICE_ISSUED'
        # Ensure this method is called whenever an item or parcel status changes significantly.
        # A more robust way is often to have signals on OrderItem and Parcel update the Order.


    def save(self, *args, **kwargs):
        if self.erp_order_id is not None and not isinstance(self.erp_order_id, str):
            self.erp_order_id = str(self.erp_order_id)
        if not self.order_display_code:
            prefix = "ORD-"
            if self.warehouse:
                prefix = self.warehouse.name[:3].upper() + "-" if len(self.warehouse.name) >=3 else "WH-"
            unique_id_part = str(uuid.uuid4().hex)[:6].upper()
            erp_id_part = self.erp_order_id[:8] if self.erp_order_id else "NOID"
            potential_code = f"{prefix}{erp_id_part}-{unique_id_part}"
            max_attempts = 5
            attempt = 0
            while Order.objects.filter(order_display_code=potential_code).exclude(pk=self.pk).exists() and attempt < max_attempts:
                attempt += 1
                unique_id_part = str(uuid.uuid4().hex)[:6+attempt].upper()
                potential_code = f"{prefix}{erp_id_part}-{unique_id_part}"
            if not Order.objects.filter(order_display_code=potential_code).exclude(pk=self.pk).exists():
                 self.order_display_code = potential_code
            else:
                self.order_display_code = f"ERR-DUP-{str(uuid.uuid4().hex)[:8].upper()}"
        # self.update_status_based_on_items_and_parcels() # Call this before super().save() if it makes sense
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    # Item-centric statuses
    ITEM_STATUS_CHOICES = [
        ('PENDING_PROCESSING', 'Pending Processing'), # Initial state for a new item
        ('ALLOCATED', 'Stock Allocated'),
        ('PACKED', 'Packed in Parcel'),
        ('ITEM_SHIPPED', 'Item Shipped'), # When the parcel containing this item is shipped
        ('ITEM_DELIVERED', 'Item Delivered'), # Parcel delivered
        ('ITEM_DELIVERY_FAILED', 'Item Delivery Failed'), # Parcel delivery failed, item not restocked
        ('ITEM_RETURNED_RESTOCKED', 'Item Returned to Stock'), # Item returned by courier and restocked
        ('ITEM_CANCELLED', 'Item Cancelled'), # e.g., for ADJUSTED_TO_CLOSE orders
        ('ITEM_BILLED', 'Item Billed') # If tracking item-level billing
    ]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, help_text="The generic product.")
    warehouse_product = models.ForeignKey(WarehouseProduct,on_delete=models.PROTECT, null=True, blank=True, help_text="The specific warehouse stock item for this product.")
    erp_product_name = models.CharField(max_length=255, blank=True, help_text="Product name as it appeared in the ERP/uploaded file.")
    quantity_ordered = models.PositiveIntegerField()
    quantity_allocated = models.PositiveIntegerField(default=0)
    quantity_packed = models.PositiveIntegerField(default=0)
    quantity_shipped = models.PositiveIntegerField(default=0)
    quantity_returned_to_stock = models.PositiveIntegerField(default=0) # New field
    suggested_batch_item = models.ForeignKey(InventoryBatchItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='allocated_order_items', help_text="The specific batch suggested or allocated for this order item.")
    suggested_batch_number_display = models.CharField(max_length=100, blank=True, null=True)
    suggested_batch_expiry_date_display = models.DateField(null=True, blank=True)
    is_cold_item = models.BooleanField(default=False, help_text="Does this specific item require cold chain handling (from ERP file).")
    status = models.CharField(max_length=30, choices=ITEM_STATUS_CHOICES, default='PENDING_PROCESSING') # Adjusted default and max_length
    notes = models.TextField(blank=True, null=True, help_text="Notes specific to this order item processing.")

    class Meta:
        ordering = ['order', 'product__name']
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"

    def __str__(self):
        return f"{self.quantity_ordered} x {self.product.name if self.product else self.erp_product_name} ({self.get_status_display()})"

    @property
    def quantity_remaining_to_pack(self):
        return self.quantity_allocated - self.quantity_packed

    @property
    def quantity_remaining_to_ship(self):
        return self.quantity_ordered - self.quantity_shipped


class Parcel(models.Model):
    # Parcel statuses are simpler: what is the state of THIS parcel?
    PARCEL_STATUS_CHOICES = [
        ('PENDING_SHIPMENT', 'Pending Shipment'), # Created, items packed, not yet handed to courier
        ('IN_TRANSIT', 'In Transit'),           # Handed to courier
        ('DELIVERED', 'Delivered'),             # Confirmed delivery
        ('DELIVERY_FAILED', 'Delivery Failed'),   # Attempted, but failed
        ('RETURNED_COURIER', 'Returned by Courier'),# Courier is returning it
        ('CANCELLED', 'Cancelled'),             # Parcel shipment cancelled before it went out
    ]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='parcels')
    parcel_code_system = models.CharField(max_length=50, unique=True, blank=True, help_text="System's unique code for this specific parcel.")
    courier_name = models.CharField(max_length=100, blank=True, null=True)
    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=25, choices=PARCEL_STATUS_CHOICES, default='PENDING_SHIPMENT') # New status field for Parcel
    shipped_at = models.DateTimeField(null=True, blank=True) # When it was actually handed to courier
    delivered_at = models.DateTimeField(null=True, blank=True) # New field for delivery confirmation
    returned_at = models.DateTimeField(null=True, blank=True) # New field for return confirmation
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_parcels')

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Shipment Parcel"
        verbose_name_plural = "Shipment Parcels"

    def __str__(self):
        return f"Parcel {self.parcel_code_system} ({self.get_status_display()}) for Order {self.order.erp_order_id}"

    def save(self, *args, **kwargs):
        if not self.parcel_code_system:
            while True:
                wh_name = self.order.warehouse.name if self.order and self.order.warehouse else None
                erp_id = self.order.erp_order_id if self.order else None
                new_code = generate_parcel_code(warehouse_name=wh_name, order_erp_id=erp_id)
                if not Parcel.objects.filter(parcel_code_system=new_code).exclude(pk=self.pk).exists():
                    self.parcel_code_system = new_code
                    break
        super().save(*args, **kwargs)
        # Potentially trigger order status update here if parcel status change implies it.
        # For example, if all parcels are 'DELIVERED', order becomes 'DELIVERED'.
        # If any parcel is 'RETURNED_COURIER', order might become 'RETURNED_COURIER'.
        # self.order.update_status_based_on_items_and_parcels()
        # self.order.save()


class ParcelItem(models.Model):
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name='items_in_parcel')
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name='shipments')
    shipped_from_batch = models.ForeignKey(InventoryBatchItem,on_delete=models.SET_NULL, null=True, blank=True, help_text="The specific inventory batch this parcel item was shipped from.")
    quantity_shipped_in_this_parcel = models.PositiveIntegerField()

    class Meta:
        ordering = ['parcel', 'order_item']
        verbose_name = "Parcel Item"
        verbose_name_plural = "Parcel Items"
        unique_together = [['parcel', 'order_item', 'shipped_from_batch']]

    def __str__(self):
        product_name_display = self.order_item.product.name if self.order_item and self.order_item.product else (self.order_item.erp_product_name if self.order_item else "N/A")
        return f"{self.quantity_shipped_in_this_parcel} x {product_name_display} in Parcel {self.parcel.parcel_code_system}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        is_new = self._state.adding
        old_quantity = 0
        if not is_new and self.pk:
            try:
                old_item = ParcelItem.objects.get(pk=self.pk)
                old_quantity = old_item.quantity_shipped_in_this_parcel
            except ParcelItem.DoesNotExist:
                pass
        super().save(*args, **kwargs)

        if self.order_item:
            quantity_diff = self.quantity_shipped_in_this_parcel - old_quantity
            # quantity_packed is the sum of quantities in all parcels for this order_item, regardless of parcel status
            current_total_packed_for_item = ParcelItem.objects.filter(order_item=self.order_item).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            self.order_item.quantity_packed = current_total_packed_for_item

            # quantity_shipped is the sum of quantities in SHIPPED/DELIVERED/FAILED parcels
            current_total_shipped_for_item = ParcelItem.objects.filter(
                order_item=self.order_item,
                parcel__status__in=['IN_TRANSIT', 'DELIVERED', 'DELIVERY_FAILED', 'RETURNED_COURIER'] # Consider what statuses mean "shipped from inventory"
            ).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            self.order_item.quantity_shipped = current_total_shipped_for_item

            # Update OrderItem status based on its own quantities and the parcel's status
            if self.parcel.status == 'DELIVERED':
                self.order_item.status = 'ITEM_DELIVERED'
            elif self.parcel.status == 'DELIVERY_FAILED':
                self.order_item.status = 'ITEM_DELIVERY_FAILED'
            elif self.parcel.status == 'RETURNED_COURIER':
                 # Logic for ITEM_RETURNED_RESTOCKED would happen when the return is processed, not just parcel status change
                pass # Handled by a separate "process return" action
            elif self.parcel.status == 'IN_TRANSIT':
                self.order_item.status = 'ITEM_SHIPPED'
            elif self.order_item.quantity_packed >= self.order_item.quantity_ordered :
                 self.order_item.status = 'PACKED' # All ordered quantity is in some parcel(s)
            elif self.order_item.quantity_packed > 0:
                 self.order_item.status = 'PACKED' # Partially packed
            elif self.order_item.quantity_allocated > 0:
                 self.order_item.status = 'ALLOCATED'
            else:
                 self.order_item.status = 'PENDING_PROCESSING'

            self.order_item.save()
            self.order_item.order.save() # To trigger order's own status update logic

    @transaction.atomic
    def delete(self, *args, **kwargs):
        oi = self.order_item
        qty_in_this_parcel_item = self.quantity_shipped_in_this_parcel
        parcel_was_shipped = self.parcel.shipped_at is not None # Check before super().delete()

        super().delete(*args, **kwargs)

        if oi:
            # Recalculate packed and shipped quantities from remaining ParcelItems
            current_total_packed_for_item = ParcelItem.objects.filter(order_item=oi).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            oi.quantity_packed = current_total_packed_for_item

            current_total_shipped_for_item = ParcelItem.objects.filter(
                order_item=oi,
                parcel__status__in=['IN_TRANSIT', 'DELIVERED', 'DELIVERY_FAILED', 'RETURNED_COURIER']
            ).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            oi.quantity_shipped = current_total_shipped_for_item

            # Simplified OrderItem status update after deleting a parcel item
            if oi.quantity_shipped >= oi.quantity_ordered:
                oi.status = 'ITEM_SHIPPED' # (or DELIVERED if that's tracked and known)
            elif oi.quantity_packed >= oi.quantity_ordered:
                oi.status = 'PACKED'
            elif oi.quantity_packed > 0:
                 oi.status = 'PACKED' # (implies partially packed for the order item)
            elif oi.quantity_allocated > 0 :
                oi.status = 'ALLOCATED'
            else:
                oi.status = 'PENDING_PROCESSING'
            oi.save()
            oi.order.save() # To trigger order's own status update logic
