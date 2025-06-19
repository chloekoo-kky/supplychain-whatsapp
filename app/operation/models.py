# app/operation/models.py
from django.db import models, transaction as db_transaction
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Q, F
import uuid
import random
import string
import logging
from decimal import Decimal

from inventory.models import Product, InventoryBatchItem, StockTransaction, PackagingMaterial
from warehouse.models import Warehouse, WarehouseProduct
from customers.models import Customer

logger = logging.getLogger(__name__)

def generate_parcel_code(warehouse_name=None, order_erp_id=None, order_date=None):
    date_to_use = order_date if order_date else timezone.now().date()
    month_letter_map = {
        1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E', 6: 'F',
        7: 'G', 8: 'H', 9: 'I', 10: 'J', 11: 'K', 12: 'L'
    }
    month_as_letter = month_letter_map.get(date_to_use.month, 'X') # X for unknown month
    day_as_string = str(date_to_use.day).zfill(2) # Ensures two digits for day, e.g., 07, 22

    prefix_parts = []
    if warehouse_name and warehouse_name.strip():
        prefix_parts.append(warehouse_name.strip()[0].upper()) # First letter of warehouse name
    else:
        prefix_parts.append("X") # Default if no warehouse name

    prefix_parts.append(month_as_letter)
    prefix_parts.append(day_as_string)

    first_part = "".join(prefix_parts)

    hex_chars = "123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
    random_part = ''.join(random.choices(hex_chars, k=4))
    return f"{first_part}-{random_part}"

class CourierCompany(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True, blank=True, null=True, help_text="Short code for the courier, e.g., DHL, FEDEX")
    is_active = models.BooleanField(default=True, help_text="Uncheck this to hide the courier from selection lists.") # ADD THIS LINE
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Courier Company"
        verbose_name_plural = "Courier Companies"

    def __str__(self):
        return self.name if not self.code else f"{self.name} ({self.code})"

class Order(models.Model):
    STATUS_CHOICES = [
        ('NEW_ORDER', 'New'),
        ('PARTIALLY_SHIPPED', 'Partial'),
        ('FULLY_SHIPPED', 'Shipped'), # This means all items are packed and in parcels
        ('ADJUSTED_TO_CLOSE', 'Adjusted to Close'),
    ]

    items_removed_log = models.JSONField(
        null=True, blank=True,
        help_text="Log of items quantities 'removed' by user during editing of partially shipped orders. Stores a list of dicts, e.g., [{'order_item_id': id, 'product_sku': sku, 'removed_qty': X}, ...]."
    )

    erp_order_id = models.CharField(max_length=50, unique=True, help_text="Order ID from the ERP system or uploaded file.")
    order_date = models.DateField(help_text="Date the order was placed, from the ERP file.")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, help_text="Warehouse associated with this order.")
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT, # Protects customer from being deleted if they have orders
        related_name='orders',
        null=True, # We will make this non-nullable after migrating existing data
        blank=True
    )
    # customer_name = models.CharField(max_length=255, blank=True, help_text="Customer's name (Address name from file).")
    # company_name = models.CharField(max_length=255, blank=True, null=True, help_text="Customer's company name (from file).")
    # recipient_address_line1 = models.CharField(max_length=255, blank=True, help_text="Street address from file.")
    # recipient_address_city = models.CharField(max_length=100, blank=True)
    # recipient_address_state = models.CharField(max_length=100, blank=True)
    # recipient_address_zip = models.CharField(max_length=20, blank=True)
    # recipient_address_country = models.CharField(max_length=100, blank=True)
    # recipient_phone = models.CharField(max_length=30, blank=True, null=True)
    # vat_number = models.CharField(max_length=50, blank=True, null=True)
    title_notes = models.TextField(blank=True, null=True, help_text="Title/General notes from the file.")
    shipping_notes = models.TextField(blank=True, null=True, help_text="Shipping specific notes from the file (used for Parcel Notes).")
    order_display_code = models.CharField(
        max_length=50,
        unique=False,
        blank=True,
        null=True,
        help_text="Display code for this order. May be deprecated or generated upon packing."
    )
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
        return f"Order {self.erp_order_id} ({self.order_display_code or 'No Display Code'}) - {self.get_status_display()}"

    def get_total_removed_quantity_for_item(self, order_item_id):
        if not self.items_removed_log:
            return 0
        if not isinstance(self.items_removed_log, list):
            logger.warning(f"Order {self.erp_order_id}: items_removed_log is not a list (type: {type(self.items_removed_log)}). Value: {self.items_removed_log}")
            return 0
        total_removed = 0
        for entry in self.items_removed_log:
            if not isinstance(entry, dict):
                logger.warning(f"Order {self.erp_order_id}: Entry in items_removed_log is not a dict (type: {type(entry)}). Value: {entry}")
                continue
            if entry.get('order_item_id') == order_item_id:
                removed_qty = entry.get('removed_qty', 0)
                if not isinstance(removed_qty, (int, float)):
                    logger.warning(f"Order {self.erp_order_id}: removed_qty for order_item_id {order_item_id} is not a number (type: {type(removed_qty)}). Value: {removed_qty}")
                    removed_qty = 0
                total_removed += removed_qty
        return total_removed

    def update_status_based_on_items_and_parcels(self):
        current_status_before_update = self.status
        logger.debug(f"Order {self.erp_order_id}: Starting update_status. Current status: {current_status_before_update}")

        terminal_statuses = ['DELIVERED', 'ADJUSTED_TO_CLOSE', 'INVOICE_ISSUED', 'CANCELLED']
        if current_status_before_update in terminal_statuses and not (current_status_before_update in ['RETURNED_COURIER', 'DELIVERY_FAILED'] and self.items.filter(status__in=['PENDING_PROCESSING', 'PACKED', 'ITEM_SHIPPED']).exists()):
            logger.debug(f"Order {self.erp_order_id}: In terminal status {current_status_before_update}. No status change.")
            return

        if not self.items.exists():
            self.status = 'NEW_ORDER'
            if current_status_before_update != self.status:
                logger.info(f"Order {self.erp_order_id} status updated to {self.get_status_display()} (no items). Prev: {current_status_before_update}")
            self.save(update_fields=['status'])
            return

        all_items_accounted_for = True
        any_item_partially_or_fully_packed = False

        for item in self.items.all():
            total_removed_for_item = self.get_total_removed_quantity_for_item(item.id)
            effective_ordered_qty = item.quantity_ordered
            if (item.quantity_packed + total_removed_for_item) < effective_ordered_qty:
                all_items_accounted_for = False
            if item.quantity_packed > 0:
                any_item_partially_or_fully_packed = True

        new_status_candidate = self.status
        if all_items_accounted_for:
            if any_item_partially_or_fully_packed:
                 new_status_candidate = 'FULLY_SHIPPED'
            elif self.status == 'PARTIALLY_SHIPPED':
                 new_status_candidate = 'FULLY_SHIPPED'
        elif any_item_partially_or_fully_packed or (self.items_removed_log and len(self.items_removed_log) > 0):
             new_status_candidate = 'PARTIALLY_SHIPPED'
        else:
             new_status_candidate = 'NEW_ORDER'

        if self.status != new_status_candidate:
            self.status = new_status_candidate
            logger.info(f"Order {self.erp_order_id} status auto-updated (considering removed items): {current_status_before_update} -> {self.get_status_display()}.")
        else:
            logger.debug(f"Order {self.erp_order_id}: Status remains {self.get_status_display()} (considering removed items).")

    def save(self, *args, **kwargs):
        if self.erp_order_id is not None and not isinstance(self.erp_order_id, str):
            self.erp_order_id = str(self.erp_order_id)
        if not self._state.adding:
             self.update_status_based_on_items_and_parcels()
        super().save(*args, **kwargs)

class OrderItem(models.Model):
    ITEM_STATUS_CHOICES = [
        ('PENDING_PROCESSING', 'Pending Processing'),
        ('PACKED', 'Packed in Parcel'),
        ('ITEM_SHIPPED', 'Item Shipped'),
        ('ITEM_DELIVERED', 'Item Delivered'),
        ('ITEM_DELIVERY_FAILED', 'Item Delivery Failed'),
        ('ITEM_RETURNED_RESTOCKED', 'Item Returned to Stock'),
        ('ITEM_CANCELLED', 'Item Cancelled'),
        ('ITEM_BILLED', 'Item Billed')
    ]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, help_text="The generic product.")
    warehouse_product = models.ForeignKey(WarehouseProduct,on_delete=models.PROTECT, null=True, blank=True, help_text="The specific warehouse stock item for this product.")
    erp_product_name = models.CharField(max_length=255, blank=True, help_text="Product name as it appeared in the ERP/uploaded file.")
    quantity_ordered = models.PositiveIntegerField()
    quantity_packed = models.PositiveIntegerField(default=0)
    quantity_shipped = models.PositiveIntegerField(default=0)
    quantity_returned_to_stock = models.PositiveIntegerField(default=0)
    suggested_batch_item = models.ForeignKey(
        InventoryBatchItem,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='allocated_order_items',
        help_text="The specific batch chosen and packed for this order item (if applicable)."
    )
    suggested_batch_number_display = models.CharField(max_length=100, blank=True, null=True)
    suggested_batch_expiry_date_display = models.DateField(null=True, blank=True)
    is_cold_item = models.BooleanField(default=False, help_text="Does this specific item require cold chain handling (from ERP file).")
    status = models.CharField(max_length=30, choices=ITEM_STATUS_CHOICES, default='PENDING_PROCESSING')
    notes = models.TextField(blank=True, null=True, help_text="Notes specific to this order item processing.")

    class Meta:
        ordering = ['order', 'product__name']
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"

    def __str__(self):
        return f"{self.quantity_ordered} x {self.product.name if self.product else self.erp_product_name} ({self.get_status_display()})"

    @property
    def quantity_remaining_to_pack(self):
        return self.quantity_ordered - self.quantity_packed

    @property
    def quantity_effectively_shipped(self):
        return self.quantity_shipped - self.quantity_returned_to_stock

    def save(self, *args, **kwargs):
        skip_order_update_flag = kwargs.pop('skip_order_update', False)
        super().save(*args, **kwargs)
        if not skip_order_update_flag and self.order:
             self.order.save()

class Parcel(models.Model):

    STATUS_CHOICES = [
        ('PREPARING_TO_PACK', 'Preparing to Pack'),
        ('READY_TO_SHIP', 'Ready to Ship'),
        ('PICKED_UP', 'Picked Up by Courier'),
        ('IN_TRANSIT', 'In Transit'),
        ('DELIVERED', 'Delivered'),
        ('DELIVERY_FAILED', 'Delivery Failed'),
        ('RETURNED_COURIER', 'Returned by Courier'),
        ('CANCELLED', 'Cancelled'),
        ('BILLED', 'Billed'),
    ]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='parcels')
    parcel_code_system = models.CharField(max_length=50, unique=True, blank=True, help_text="System's unique code for this specific parcel.")
    courier_company = models.ForeignKey(
        'operation.CourierCompany',
        on_delete=models.SET_NULL, # Or models.PROTECT if a courier should not be deletable while linked to a parcel
        null=True,
        blank=True,
        related_name='parcels'
    )
    tracking_number = models.CharField(max_length=100, blank=True, null=True)

    packaging_type = models.ForeignKey(
        'operation.PackagingType',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text="The new packaging type relationship."
    )

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='PREPARING_TO_PACK')
    shipped_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when parcel status changed to PICKED_UP.")
    delivered_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when parcel status changed to RETURNED_COURIER.")
    cancelled_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when parcel status changed to CANCELLED.")
    billed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_parcels')

    # New fields for customs details
    weight = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, verbose_name="Weight (kg)")
    customs_declaration = models.ForeignKey(
        'operation.CustomsDeclaration',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='parcels',
        help_text="The selected customs declaration for this parcel."
    )
    declared_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Total declared value for this parcel for customs.")
    declared_value_myr = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Declared Value (MYR)",
        help_text="Automatically calculated declared value in MYR based on the USD value."
    )
    length = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, verbose_name="Length (cm)")
    width = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, verbose_name="Width (cm)")
    height = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, verbose_name="Height (cm)")
    dimensional_weight_kg = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, verbose_name="Dimensional Weight (kg)")
    estimated_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Estimated shipping cost for this parcel."
    )


    class Meta:
        ordering = ['-created_at']
        verbose_name = "Shipment Parcel"
        verbose_name_plural = "Shipment Parcels"

    def __str__(self):
        return f"Parcel {self.parcel_code_system} ({self.get_status_display()}) for Order {self.order.erp_order_id}"

    @property
    def simplified_tracking_status(self):
        """
        Returns a simplified status string based on the latest tracking log
        and the time since the parcel was shipped.
        """
        if not self.tracking_number:
            return ""

        latest_log = self.tracking_logs.first()

        if not latest_log:
            return "Awaiting Tracking Update"

        # Rule 1: If the latest status description contains "Delivered"
        if 'delivered' in latest_log.status_description.lower():
            return "Delivered"

        # Rule 3: If status is "In Transit" for more than 20 days
        if self.shipped_at:
            days_in_transit = (timezone.now() - self.shipped_at).days
            if days_in_transit > 20:
                return "Failed"

        # Rule 2: If none of the above, it's "In Transit"
        return "In Transit"

    def get_packaging_type_display(self):
        if self.packaging_type:
            return self.packaging_type.name
        return "N/A"

    def save(self, *args, **kwargs):
        old_status = None
        is_new_parcel = self._state.adding
        original_tracking_number = None

        if not is_new_parcel and self.pk:
            try:
                previous_instance = Parcel.objects.get(pk=self.pk)
                old_status = previous_instance.status
                original_tracking_number = previous_instance.tracking_number
            except Parcel.DoesNotExist:
                pass

        if not self.parcel_code_system:
            while True:
                wh_name = self.order.warehouse.name if self.order and self.order.warehouse else None
                erp_id = self.order.erp_order_id if self.order else None
                new_code = generate_parcel_code(warehouse_name=wh_name, order_erp_id=erp_id)
                if not Parcel.objects.filter(parcel_code_system=new_code).exclude(pk=self.pk).exists():
                    self.parcel_code_system = new_code
                    break

        if self.status == 'PREPARING_TO_PACK' and self.tracking_number and self.courier_company:
            if is_new_parcel or (old_status == 'PREPARING_TO_PACK' and not original_tracking_number):
                self.status = 'READY_TO_SHIP'
                logger.info(f"Parcel {self.parcel_code_system}: Status auto-changed to READY_TO_SHIP.")

        if self.status == 'PICKED_UP' and (old_status != 'PICKED_UP' or not self.shipped_at):
            self.shipped_at = timezone.now()
        elif self.status == 'RETURNED_COURIER' and (old_status != 'RETURNED_COURIER' or not self.returned_at):
            self.returned_at = timezone.now()
        elif self.status == 'CANCELLED' and (old_status != 'CANCELLED' or not self.cancelled_at):
            self.cancelled_at = timezone.now()
        elif self.status == 'DELIVERED' and (old_status != 'DELIVERED' or not self.delivered_at):
            self.delivered_at = timezone.now()
        elif self.status == 'BILLED' and (old_status != 'BILLED' or not self.billed_at):
            self.billed_at = timezone.now()

        stock_deducted_states = ['PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'DELIVERY_FAILED']
        is_status_change_to_return_or_cancel = self.status in ['RETURNED_COURIER', 'CANCELLED']
        was_stock_previously_deducted = old_status in stock_deducted_states
        needs_stock_return = is_status_change_to_return_or_cancel and was_stock_previously_deducted

        if needs_stock_return:
            logger.info(f"Parcel {self.id} status changed from {old_status} to {self.status}. Processing stock return.")
            with db_transaction.atomic():
                for item_in_parcel in self.items_in_parcel.select_related('shipped_from_batch__warehouse_product__warehouse', 'shipped_from_batch__warehouse_product__product', 'order_item').all():
                    if item_in_parcel.shipped_from_batch and item_in_parcel.quantity_shipped_in_this_parcel > 0:
                        if item_in_parcel.order_item.status == 'ITEM_RETURNED_RESTOCKED' and old_status == self.status:
                             logger.warning(f"Stock for OI {item_in_parcel.order_item.id} in ParcelItem {item_in_parcel.id} seems already restocked. Skipping additional return.")
                             continue
                        batch = item_in_parcel.shipped_from_batch
                        returned_qty = item_in_parcel.quantity_shipped_in_this_parcel
                        original_batch_qty = batch.quantity
                        batch.quantity = F('quantity') + returned_qty
                        batch.save(update_fields=['quantity'])
                        batch.refresh_from_db()
                        logger.info(f"Batch {batch.id} quantity: {original_batch_qty} -> {batch.quantity} (+{returned_qty})")
                        StockTransaction.objects.create(
                            warehouse=batch.warehouse_product.warehouse,
                            warehouse_product=batch.warehouse_product,
                            product=batch.warehouse_product.product,
                            transaction_type='RETURN_IN',
                            quantity=returned_qty,
                            reference_note=f"{self.get_status_display()} - P:{self.parcel_code_system}, O:{self.order.erp_order_id}, B:{batch.batch_number}",
                            related_order=self.order
                        )
                        order_item = item_in_parcel.order_item
                        original_oi_returned_qty = order_item.quantity_returned_to_stock
                        order_item.quantity_returned_to_stock = F('quantity_returned_to_stock') + returned_qty
                        order_item.status = 'ITEM_RETURNED_RESTOCKED'
                        order_item.quantity_shipped = F('quantity_shipped') - returned_qty
                        order_item.save(update_fields=['quantity_returned_to_stock', 'quantity_shipped', 'status'])
                        order_item.refresh_from_db()
                        logger.info(f"OI {order_item.id} returned_qty: {original_oi_returned_qty} -> {order_item.quantity_returned_to_stock}, shipped_qty reduced, status -> ITEM_RETURNED_RESTOCKED")
                    else:
                        logger.warning(f"ParcelItem {item_in_parcel.id} for Parcel {self.id} has no batch or zero quantity, skipping stock return.")

        if self.declared_value is not None:
            conversion_rate = Decimal('4.3')
            myr_value = self.declared_value * conversion_rate
            # Round to 2 decimal places for currency
            self.declared_value_myr = myr_value.quantize(Decimal('0.01'))
        else:
            self.declared_value_myr = None

        super().save(*args, **kwargs)

        if old_status != self.status or is_new_parcel:
            for pi in self.items_in_parcel.all():
                pi.save()
        elif self.order:
            self.order.save()

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

    # New fields for customs details per item
    customs_description = models.CharField(max_length=255, null=True, blank=True, help_text="Specific description for this item line for customs.")
    declared_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Declared value for this specific item line in the parcel.")

    class Meta:
        ordering = ['parcel', 'order_item']
        verbose_name = "Parcel Item"
        verbose_name_plural = "Parcel Items"

    def __str__(self):
        product_name_display = self.order_item.product.name if self.order_item and self.order_item.product else (self.order_item.erp_product_name if self.order_item else "N/A")
        return f"{self.quantity_shipped_in_this_parcel} x {product_name_display} in Parcel {self.parcel.parcel_code_system}"

    @db_transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.order_item:
            current_total_packed_for_item = ParcelItem.objects.filter(
                order_item=self.order_item
            ).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            self.order_item.quantity_packed = current_total_packed_for_item
            current_total_shipped_for_item = ParcelItem.objects.filter(
                order_item=self.order_item,
                parcel__status__in=['PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'DELIVERY_FAILED']
            ).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            self.order_item.quantity_shipped = current_total_shipped_for_item
            if self.order_item.status not in ['ITEM_RETURNED_RESTOCKED', 'ITEM_CANCELLED', 'ITEM_BILLED']:
                if self.parcel.status == 'DELIVERED':
                    self.order_item.status = 'ITEM_DELIVERED'
                elif self.parcel.status == 'DELIVERY_FAILED':
                    self.order_item.status = 'ITEM_DELIVERY_FAILED'
                elif self.parcel.status in ['PICKED_UP', 'IN_TRANSIT']:
                    self.order_item.status = 'ITEM_SHIPPED'
                elif self.parcel.status in ['PREPARING_TO_PACK', 'READY_TO_SHIP']:
                    if self.order_item.quantity_packed >= self.order_item.quantity_ordered:
                        self.order_item.status = 'PACKED'
                    elif self.order_item.quantity_packed > 0:
                        self.order_item.status = 'PACKED'
                    else:
                        self.order_item.status = 'PENDING_PROCESSING'
                elif self.parcel.status == 'BILLED':
                    if self.order_item.status not in ['ITEM_DELIVERED']:
                        self.order_item.status = 'ITEM_BILLED'
                elif self.order_item.quantity_packed == 0 :
                     self.order_item.status = 'PENDING_PROCESSING'
            self.order_item.save(skip_order_update=True)
            self.order_item.order.save()

    @db_transaction.atomic
    def delete(self, *args, **kwargs):
        oi = self.order_item
        super().delete(*args, **kwargs)
        if oi:
            current_total_packed_for_item = ParcelItem.objects.filter(order_item=oi).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            oi.quantity_packed = current_total_packed_for_item
            current_total_shipped_for_item = ParcelItem.objects.filter(
                order_item=oi,
                parcel__status__in=['PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'DELIVERY_FAILED']
            ).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            oi.quantity_shipped = current_total_shipped_for_item
            if oi.status not in ['ITEM_RETURNED_RESTOCKED', 'ITEM_CANCELLED', 'ITEM_BILLED', 'ITEM_DELIVERED']:
                if oi.quantity_packed == 0:
                    oi.status = 'PENDING_PROCESSING'
                else:
                    oi.status = 'PACKED'
            oi.save(skip_order_update=True)
            oi.order.save()

class CustomsDeclaration(models.Model):
    description = models.TextField(help_text="Unique description of the goods for customs purposes.")
    hs_code = models.CharField(max_length=20, help_text="Harmonized System (HS) code.")
    courier_companies = models.ManyToManyField(
        CourierCompany,
        blank=True,
        related_name='customs_declarations',
        help_text="Select couriers this applies to. Leave blank if generic for all couriers."
    )
    applies_to_mix = models.BooleanField(default=False, verbose_name="Applies to Mixed Shipments (Cold + Ambient)")
    applies_to_ambient = models.BooleanField(default=False, verbose_name="Applies to Ambient Only Shipments")
    applies_to_cold_chain = models.BooleanField(default=False, verbose_name="Applies to Cold Chain Only Shipments")

    notes = models.TextField(blank=True, null=True, help_text="Optional notes.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['description']
        verbose_name = "Customs Declaration"
        verbose_name_plural = "Customs Declarations"
        constraints = [
            models.UniqueConstraint(fields=['description', 'hs_code'], name='unique_desc_hs_code_pair')
        ]

    def __str__(self):
        couriers = ", ".join([c.code for c in self.courier_companies.all()]) or "Generic"
        types = []
        if self.applies_to_ambient: types.append("Ambient")
        if self.applies_to_cold_chain: types.append("Cold")
        if self.applies_to_mix: types.append("Mix")
        type_str = ", ".join(types) or "No types"
        return f"{self.description[:40]}... (HS: {self.hs_code}) | Couriers: {couriers} | Types: {type_str}"

    def get_shipment_types_display(self):
        """Helper method for display in admin or templates."""
        types = []
        if self.applies_to_ambient: types.append("Ambient")
        if self.applies_to_cold_chain: types.append("Cold Chain")
        if self.applies_to_mix: types.append("Mixed")
        if not types: return "None"
        return ", ".join(types)



class PackagingType(models.Model):
    ENVIRONMENT_CHOICES = [
        ('COLD', 'Cold Chain'),
        ('AMBIENT', 'Ambient'),
        # Add more as needed
    ]

    name = models.CharField(max_length=150, unique=True)
    type_code = models.CharField(max_length=50, unique=True, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    environment_type = models.CharField(max_length=20, choices=ENVIRONMENT_CHOICES, default='AMBIENT')

    default_length_cm = models.DecimalField(
            max_digits=10, decimal_places=2,
            help_text="",
            null=True, blank=True
        )
    default_width_cm = models.DecimalField(
            max_digits=10, decimal_places=2,
            help_text="",
            null=True, blank=True
        )
    default_height_cm = models.DecimalField(
            max_digits=10, decimal_places=2,
            help_text="",
            null=True, blank=True
        )

    materials = models.ManyToManyField(
        PackagingMaterial,
        through='operation.PackagingTypeMaterialComponent',
        related_name='packaging_types',
        blank=True
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.get_environment_type_display()})"

    class Meta:
        verbose_name = "Packaging Type"
        verbose_name_plural = "Packaging Types"
        ordering = ['name']

class PackagingTypeMaterialComponent(models.Model):
    packaging_type = models.ForeignKey(PackagingType, on_delete=models.CASCADE)
    packaging_material = models.ForeignKey(PackagingMaterial, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1, help_text="Quantity of this material used in the packaging type.")

    class Meta:
        unique_together = ('packaging_type', 'packaging_material')
        verbose_name = "Packaging Type Material Component"
        verbose_name_plural = "Packaging Type Material Components"

    def __str__(self):
        pm_name = self.packaging_material.name if hasattr(self, 'packaging_material') and self.packaging_material else "N/A"
        pt_name = self.packaging_type.name if hasattr(self, 'packaging_type') and self.packaging_type else "N/A"
        return f"{self.quantity} x {pm_name} for {pt_name}"


class ParcelTrackingLog(models.Model):
    """
    Stores a single tracking event from a courier for a specific parcel.
    """
    parcel = models.ForeignKey('operation.Parcel', on_delete=models.CASCADE, related_name='tracking_logs')
    timestamp = models.DateTimeField(help_text="The date and time of the tracking event.")
    status_description = models.CharField(max_length=255, help_text="The description of the event, e.g., 'Departed from Facility'.")
    location = models.CharField(max_length=255, blank=True, null=True, help_text="The location where the event occurred.")

    # A unique identifier provided by the courier for this specific event to prevent duplicates.
    event_id = models.CharField(max_length=100, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        # Ensure we don't save the exact same event for the same parcel twice
        unique_together = ('parcel', 'event_id')
        verbose_name = "Parcel Tracking Log"
        verbose_name_plural = "Parcel Tracking Logs"

    def __str__(self):
        return f"{self.parcel.parcel_code_system} @ {self.timestamp}: {self.status_description}"

