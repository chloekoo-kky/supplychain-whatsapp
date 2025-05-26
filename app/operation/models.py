# app/operation/models.py
from django.db import models, transaction as db_transaction
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Q, F
import uuid
import random
import string
import logging

from inventory.models import Product, InventoryBatchItem, StockTransaction
from warehouse.models import Warehouse, WarehouseProduct

logger = logging.getLogger(__name__)

def generate_parcel_code(warehouse_name=None, order_erp_id=None):
    # ... (generate_parcel_code function remains the same) ...
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
        ('NEW_ORDER', 'New'),
        ('PARTIALLY_SHIPPED', 'Partial'),
        ('FULLY_SHIPPED', 'Shipped'), # This means all items are packed and in parcels
        ('DELIVERED', 'Delivered'),
        ('DELIVERY_FAILED', 'Failed'),
        ('RETURNED_COURIER', 'Returned by Courier'),
        ('ADJUSTED_TO_CLOSE', 'Adjusted to Close'),
        ('INVOICE_ISSUED', 'Billed (Completed)')
    ]
    # ... (All other fields of Order model remain the same) ...
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

    def update_status_based_on_items_and_parcels(self):
        current_status_before_update = self.status
        logger.debug(f"Order {self.erp_order_id}: Starting update_status. Current status: {current_status_before_update}")

        terminal_statuses = ['DELIVERED', 'ADJUSTED_TO_CLOSE', 'INVOICE_ISSUED', 'CANCELLED']
        # Allow updates if it was RETURNED or FAILED and new packing actions occur.
        if current_status_before_update in terminal_statuses:
            # Check if any item is no longer in a "final" state for a return/failure scenario
            is_reopened_for_packing = self.items.filter(status__in=['PENDING_PROCESSING', 'PACKED', 'ITEM_SHIPPED']).exists()
            if not (current_status_before_update in ['RETURNED_COURIER', 'DELIVERY_FAILED'] and is_reopened_for_packing):
                logger.debug(f"Order {self.erp_order_id}: In terminal status {current_status_before_update} and not eligible for re-opening by packing. No status change.")
                return

        if not self.items.exists():
            self.status = 'NEW_ORDER'
            if current_status_before_update != self.status:
                logger.info(f"Order {self.erp_order_id} status updated to {self.get_status_display()} (no items). Prev: {current_status_before_update}")
            return

        total_qty_ordered_for_order = sum(item.quantity_ordered for item in self.items.all())
        total_qty_packed_in_all_parcels = sum(item.quantity_packed for item in self.items.all())

        if total_qty_ordered_for_order == 0 and self.items.count() > 0:
            self.status = 'NEW_ORDER'
            if current_status_before_update != self.status:
                logger.warning(f"Order {self.erp_order_id} has items but total_qty_ordered is 0. Status set to {self.get_status_display()}. Prev: {current_status_before_update}")
            return

        parcels_exist_for_order = self.parcels.exists()
        new_status_candidate = self.status # Default to no change unless conditions below are met

        if total_qty_packed_in_all_parcels == total_qty_ordered_for_order and total_qty_ordered_for_order > 0:
            # All items are fully packed.
            # Rule 4: "only when all OrderItem fully packed, Order.status will be changed to 'Shipped'"
            # This means if all items are packed into parcels, the Order is considered 'FULLY_SHIPPED'
            # from a packing completion standpoint. The actual physical shipment progress is on the Parcel.
            if parcels_exist_for_order: # Parcels must exist if all items are packed.
                new_status_candidate = 'FULLY_SHIPPED'
            else:
                # Inconsistent state: all items claim to be packed, but no Parcel objects exist.
                new_status_candidate = 'NEW_ORDER'
                logger.error(f"Order {self.erp_order_id}: All items reported as fully packed but no parcels found. Setting to NEW_ORDER. Investigate data consistency.")

        elif total_qty_packed_in_all_parcels > 0 and total_qty_packed_in_all_parcels < total_qty_ordered_for_order:
            # Some items are packed, but not all. (QuantityPacked < QuantityOrdered)
            # Rule 1: "when QuantityPacked < QuantityOrdered, Order.status should be 'Partial'"
            if parcels_exist_for_order: # Parcels must exist if any item is packed.
                new_status_candidate = 'PARTIALLY_SHIPPED'
            else:
                # Inconsistent state: items claim to be packed, but no Parcel objects.
                new_status_candidate = 'NEW_ORDER'
                logger.error(f"Order {self.erp_order_id}: Items reported as partially packed but no parcels found. Setting to NEW_ORDER. Investigate data consistency.")

        elif total_qty_packed_in_all_parcels == 0:
            # No items packed yet for this order.
            new_status_candidate = 'NEW_ORDER'

        # If the status has changed from its state before this method was called
        if self.status != new_status_candidate:
            self.status = new_status_candidate
            logger.info(f"Order {self.erp_order_id} status automatically updated: {current_status_before_update} -> {self.get_status_display()}. Packed/Ordered: {total_qty_packed_in_all_parcels}/{total_qty_ordered_for_order}.")
        else:
            logger.debug(f"Order {self.erp_order_id}: Status remains {self.get_status_display()}. Packed/Ordered: {total_qty_packed_in_all_parcels}/{total_qty_ordered_for_order}.")


    def save(self, *args, **kwargs):
        if self.erp_order_id is not None and not isinstance(self.erp_order_id, str):
            self.erp_order_id = str(self.erp_order_id)

        if not self._state.adding:
            self.update_status_based_on_items_and_parcels()

        super().save(*args, **kwargs)


# ... (OrderItem model - no changes needed from last version for these specific issues) ...
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
    courier_name = models.CharField(max_length=100, blank=True, null=True)
    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='PREPARING_TO_PACK') # Default updated

    shipped_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when parcel status changed to PICKED_UP.")
    delivered_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when parcel status changed to RETURNED_COURIER.")
    cancelled_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when parcel status changed to CANCELLED.")
    billed_at = models.DateTimeField(null=True, blank=True)

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
        old_status = None
        is_new_parcel = self._state.adding
        original_tracking_number = None # For checking if tracking_number was newly added

        if not is_new_parcel and self.pk:
            try:
                previous_instance = Parcel.objects.get(pk=self.pk)
                old_status = previous_instance.status
                original_tracking_number = previous_instance.tracking_number # Capture original tracking
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

        # Automatic status transition: PREPARING_TO_PACK -> READY_TO_SHIP
        # If status is PREPARING_TO_PACK and tracking_number becomes non-blank (and courier_name too)
        if self.status == 'PREPARING_TO_PACK' and self.tracking_number and self.courier_name:
            if is_new_parcel or (old_status == 'PREPARING_TO_PACK' and not original_tracking_number): # Transition if new or tracking was just added
                self.status = 'READY_TO_SHIP'
                logger.info(f"Parcel {self.parcel_code_system}: Status auto-changed to READY_TO_SHIP.")

        # Timestamping based on status changes
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

        # Stock Adjustment Logic
        stock_deducted_states = ['PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'DELIVERY_FAILED']
        is_status_change_to_return_or_cancel = self.status in ['RETURNED_COURIER', 'CANCELLED']
        was_stock_previously_deducted = old_status in stock_deducted_states
        needs_stock_return = is_status_change_to_return_or_cancel and was_stock_previously_deducted

        if needs_stock_return:
            logger.info(f"Parcel {self.id} status changed from {old_status} to {self.status}. Processing stock return.")
            with db_transaction.atomic():
                for item_in_parcel in self.items_in_parcel.select_related('shipped_from_batch__warehouse_product__warehouse', 'shipped_from_batch__warehouse_product__product', 'order_item').all():
                    if item_in_parcel.shipped_from_batch and item_in_parcel.quantity_shipped_in_this_parcel > 0:
                        # Check if this item has already had its stock returned for this parcel to prevent double returns.
                        # This simple check relies on OrderItem status; a more robust flag on ParcelItem might be better.
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
                        # We must also adjust quantity_shipped and quantity_packed on the OrderItem
                        order_item.quantity_shipped = F('quantity_shipped') - returned_qty
                        # quantity_packed might not need adjustment if it represents all items ever put in any parcel for this OI.
                        # Or if packing is reversed, it should be reduced.
                        # For now, assume quantity_packed remains, indicating it *was* packed.
                        order_item.save(update_fields=['quantity_returned_to_stock', 'quantity_shipped', 'status'])
                        order_item.refresh_from_db()
                        logger.info(f"OI {order_item.id} returned_qty: {original_oi_returned_qty} -> {order_item.quantity_returned_to_stock}, shipped_qty reduced, status -> ITEM_RETURNED_RESTOCKED")
                    else:
                        logger.warning(f"ParcelItem {item_in_parcel.id} for Parcel {self.id} has no batch or zero quantity, skipping stock return.")

        super().save(*args, **kwargs)

        if old_status != self.status or is_new_parcel:
            for pi in self.items_in_parcel.all():
                pi.save()
        elif self.order:
            self.order.save()

# ... (ParcelItem model - ensure its save method updates OrderItem statuses correctly based on new Parcel statuses)
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

    def __str__(self):
        product_name_display = self.order_item.product.name if self.order_item and self.order_item.product else (self.order_item.erp_product_name if self.order_item else "N/A")
        return f"{self.quantity_shipped_in_this_parcel} x {product_name_display} in Parcel {self.parcel.parcel_code_system}"

    @db_transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.order_item:
            # Recalculate total packed for the OrderItem
            current_total_packed_for_item = ParcelItem.objects.filter(
                order_item=self.order_item
            ).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            self.order_item.quantity_packed = current_total_packed_for_item

            # Recalculate total effectively shipped (for stock "out" states)
            current_total_shipped_for_item = ParcelItem.objects.filter(
                order_item=self.order_item,
                parcel__status__in=['PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'DELIVERY_FAILED']
            ).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            self.order_item.quantity_shipped = current_total_shipped_for_item


            # Update OrderItem status
            # Only update if not already in a terminal state handled by Parcel.save (like RETURNED or CANCELLED)
            if self.order_item.status not in ['ITEM_RETURNED_RESTOCKED', 'ITEM_CANCELLED', 'ITEM_BILLED']:
                if self.parcel.status == 'DELIVERED':
                    self.order_item.status = 'ITEM_DELIVERED'
                elif self.parcel.status == 'DELIVERY_FAILED':
                    self.order_item.status = 'ITEM_DELIVERY_FAILED'
                # RETURNED_COURIER for Parcel now results in ITEM_RETURNED_RESTOCKED for OrderItem via Parcel.save()
                # elif self.parcel.status == 'RETURNED_COURIER':
                #    self.order_item.status = 'ITEM_SHIPPED'
                elif self.parcel.status in ['PICKED_UP', 'IN_TRANSIT']:
                    self.order_item.status = 'ITEM_SHIPPED'
                elif self.parcel.status in ['PREPARING_TO_PACK', 'READY_TO_SHIP']:
                    if self.order_item.quantity_packed >= self.order_item.quantity_ordered:
                        self.order_item.status = 'PACKED'
                    elif self.order_item.quantity_packed > 0:
                        self.order_item.status = 'PACKED'
                    else:
                        self.order_item.status = 'PENDING_PROCESSING'
                # CANCELLED for Parcel now results in ITEM_RETURNED_RESTOCKED or ITEM_CANCELLED via Parcel.save()
                # elif self.parcel.status == 'CANCELLED':
                #    self.order_item.status = 'ITEM_CANCELLED'
                elif self.parcel.status == 'BILLED':
                    if self.order_item.status not in ['ITEM_DELIVERED']: # Don't override delivered
                        self.order_item.status = 'ITEM_BILLED'
                elif self.order_item.quantity_packed == 0 :
                     self.order_item.status = 'PENDING_PROCESSING'

            self.order_item.save(skip_order_update=True)
            self.order_item.order.save()

    @db_transaction.atomic
    def delete(self, *args, **kwargs):
        # ... (delete logic for ParcelItem should remain largely the same,
        # ensuring it correctly recalculates OrderItem quantities and saves OrderItem & Order)
        oi = self.order_item
        # Stock is not returned here by default, only "unpacked".
        # If deleting a parcel item from a SHIPPED parcel, that's a complex case usually handled by
        # returns or cancellations at the parcel level.
        super().delete(*args, **kwargs)
        if oi:
            # Recalculate quantities
            current_total_packed_for_item = ParcelItem.objects.filter(order_item=oi).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            oi.quantity_packed = current_total_packed_for_item

            current_total_shipped_for_item = ParcelItem.objects.filter(
                order_item=oi,
                parcel__status__in=['PICKED_UP', 'IN_TRANSIT', 'DELIVERED', 'DELIVERY_FAILED']
            ).aggregate(total=Sum('quantity_shipped_in_this_parcel'))['total'] or 0
            oi.quantity_shipped = current_total_shipped_for_item

            # Update OrderItem status if not in a final user-set state
            if oi.status not in ['ITEM_RETURNED_RESTOCKED', 'ITEM_CANCELLED', 'ITEM_BILLED', 'ITEM_DELIVERED']:
                if oi.quantity_packed == 0: # If all parcel items for this order item are gone
                    oi.status = 'PENDING_PROCESSING'
                else: # Still some quantity packed in other parcels
                    oi.status = 'PACKED'
            oi.save(skip_order_update=True)
            oi.order.save()
