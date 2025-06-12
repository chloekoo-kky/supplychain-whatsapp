# app/inventory/models.py
from django.db import models, transaction as db_transaction
from django.utils import timezone
from django.conf import settings
from django.db.models import UniqueConstraint, Q, F
from django.core.exceptions import ValidationError

from warehouse.models import Warehouse, WarehouseProduct

class Supplier(models.Model):
    name = models.CharField(max_length=100, null=True)
    code = models.CharField(max_length=20, unique=True, null=True)
    address = models.TextField(null=True, blank=True)
    whatsapp_number = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(blank=True, null=True)

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name


class Product(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    created_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.sku})"

    class Meta:
        ordering = ['name']
        verbose_name = "Product"
        verbose_name_plural = "Products"


class InventoryBatchItem(models.Model):
    warehouse_product = models.ForeignKey(
        'warehouse.WarehouseProduct',
        on_delete=models.CASCADE,
        related_name="batches"
    )
    batch_number = models.CharField(
        max_length=100,
        null=True,
        blank=False,
        verbose_name="Batch Number"
    )
    location_label = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name="Label"
    )
    expiry_date = models.DateField(null=True, blank=True, verbose_name="Expiry Date")
    quantity = models.PositiveIntegerField(default=0, verbose_name="Qty")
    cost_price = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        verbose_name="Cost Price (per unit)"
    )
    date_received = models.DateField(default=timezone.now, verbose_name="Date Received")
    pick_priority = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=[(0, 'Default'), (1, 'Secondary')],
        help_text="Picking priority: 0 for Default, 1 for Secondary. Leave blank for normal FEFO."
    )
    last_modified = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['warehouse_product', 'batch_number', 'location_label']]
        constraints = [
            UniqueConstraint(
                fields=['warehouse_product'],
                condition=Q(pick_priority=0),
                name='unique_default_pick_priority_per_wp'
            ),
            UniqueConstraint(
                fields=['warehouse_product'],
                condition=Q(pick_priority=1),
                name='unique_secondary_pick_priority_per_wp'
            )
        ]
        ordering = [
            'warehouse_product__product__name',
            'warehouse_product__warehouse__name',
            F('pick_priority').asc(nulls_last=True),
            F('expiry_date').asc(nulls_last=True),
            'date_received',
            'batch_number',
            'location_label'
        ]
        verbose_name = "Inventory Batch Item"
        verbose_name_plural = "Inventory Batch Items"

    def __str__(self):
        batch_display = self.batch_number if self.batch_number else "NO_BATCH_ID"
        location_display = f" (Loc: {self.location_label})" if self.location_label else ""
        wp_display = str(self.warehouse_product) if self.warehouse_product else "N/A WarehouseProduct"
        priority_display = ""
        if self.pick_priority == 0:
            priority_display = " (Default Pick)"
        elif self.pick_priority == 1:
            priority_display = " (Secondary Pick)"
        return f"{wp_display} - Batch: {batch_display}{location_display} (Qty: {self.quantity}){priority_display}"

    def save(self, *args, **kwargs):
        if self.location_label == '':
            self.location_label = None
        if self.pick_priority is not None and self.pick_priority in [0, 1]:
            with db_transaction.atomic():
                InventoryBatchItem.objects.filter(
                    warehouse_product=self.warehouse_product,
                    pick_priority=self.pick_priority
                ).exclude(pk=self.pk).update(pick_priority=None)
        super().save(*args, **kwargs)

    @property
    def expiry_status_display(self):
        if not self.expiry_date:
            return None
        today = timezone.now().date()
        delta = self.expiry_date - today
        if delta.days < 0:
            return "Expired"
        elif 0 <= delta.days <= 180: # 6 months * 30 days approx
            return "≤6m"
        return None

class StockTransaction(models.Model):
    # --- MODIFICATION: Use TextChoices for better organization ---
    class TransactionTypes(models.TextChoices):
        STOCK_IN = 'IN', 'Stock In'
        STOCK_OUT = 'OUT', 'Stock Out' # General stock out
        ADJ_POSITIVE = 'ADJ_P', 'Positive Adjustment'
        ADJ_NEGATIVE = 'ADJ_N', 'Negative Adjustment'
        TRANSFER_OUT = 'TRANSFER_OUT', 'Transfer Out'
        TRANSFER_IN = 'TRANSFER_IN', 'Transfer In'
        RETURN_IN = 'RETURN_IN', 'Return In'
        STOCKTAKE_INITIAL = 'ST_INITIAL', 'Stock Take Initial'
        STOCKTAKE_UPDATE = 'ST_UPDATE', 'Stock Take Update'
        SALE_PACKED_OUT = 'SALE_PACKED_OUT', 'Sale - Packed Out' # Added for clarity
    # --- END MODIFICATION ---

    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.PROTECT, related_name='stock_transactions')
    warehouse_product = models.ForeignKey('warehouse.WarehouseProduct', on_delete=models.PROTECT, related_name='stock_transactions',
                                        help_text="The specific warehouse product this transaction affects.")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='stock_transactions_direct',
                                help_text="Direct link to the Product (denormalized for easier querying if needed).")

    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionTypes.choices # MODIFIED: Use choices from TextChoices
    )
    quantity = models.IntegerField(help_text="Change in quantity. Positive for stock in/positive adjustments, negative for stock out/negative adjustments.")
    batch_item_involved = models.ForeignKey(
        InventoryBatchItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_transactions',
        help_text="The specific batch item involved in this transaction, if applicable."
    )
    reference_note = models.CharField(max_length=255, blank=True, null=True, help_text="E.g., PO number, Order ID, Adjustment reason, Stock Take Session ID.")
    related_order = models.ForeignKey('operation.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_transactions')
    related_po = models.ForeignKey('warehouse.PurchaseOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_transactions')
    transaction_date = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-transaction_date']
        verbose_name = "Stock Transaction"
        verbose_name_plural = "Stock Transactions"

    def __str__(self):
        # MODIFIED: Use get_transaction_type_display for TextChoices
        return f"{self.get_transaction_type_display()} of {self.quantity} for {self.product.name} ({self.warehouse.name}) on {self.transaction_date.strftime('%Y-%m-%d %H:%M')}"

    def clean(self):
        super().clean()
        # MODIFIED: Refer to TextChoices values for validation
        positive_qty_types = [
            self.TransactionTypes.STOCK_IN, self.TransactionTypes.ADJ_POSITIVE,
            self.TransactionTypes.TRANSFER_IN, self.TransactionTypes.RETURN_IN,
            self.TransactionTypes.STOCKTAKE_INITIAL, self.TransactionTypes.STOCKTAKE_UPDATE
        ]
        negative_qty_types = [
            self.TransactionTypes.STOCK_OUT, self.TransactionTypes.ADJ_NEGATIVE,
            self.TransactionTypes.TRANSFER_OUT, self.TransactionTypes.SALE_PACKED_OUT
        ]

        if self.transaction_type in positive_qty_types and self.quantity < 0:
            raise ValidationError(f"{self.get_transaction_type_display()} transactions must have a non-negative quantity.")
        if self.transaction_type in negative_qty_types and self.quantity > 0:
            # For outgoing stock, quantity should be stored as negative.
            # If user inputs positive, it should be converted or validated.
            # Assuming quantity should be negative for these types.
            raise ValidationError(f"{self.get_transaction_type_display()} transactions typically have a non-positive quantity (or zero if it represents a 'before' state).")


        if self.warehouse_product and self.product != self.warehouse_product.product:
            raise ValidationError("Product in WarehouseProduct and direct Product link must match.")
        if self.batch_item_involved and self.warehouse_product != self.batch_item_involved.warehouse_product:
            raise ValidationError("WarehouseProduct of StockTransaction and BatchItemInvolved must match.")



class StockTakeSession(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Operator Input'),
        ('COMPLETED_BY_OPERATOR', 'Completed'),
        ('EVALUATED', 'Evaluated by Superuser'),
        ('CLOSED', 'Closed/Archived'),
    ]
    name = models.CharField(max_length=255, help_text="e.g., Full Stock Take - 2025-05-15 - Main Warehouse")
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        help_text="The warehouse where the stock take was performed."
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='stock_takes_initiated',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text="User who started or is responsible for this stock take session."
    )
    initiated_at = models.DateTimeField(default=timezone.now, help_text="Timestamp when the stock take session was created/initiated.")
    completed_by_operator_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when the warehouse operator marked their counting as complete.")
    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='stock_takes_evaluated',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text="Superuser who evaluated this stock take."
    )
    evaluated_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when the stock take was evaluated.")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING', help_text="Current status of the stock take session.")
    notes = models.TextField(blank=True, help_text="General notes for this stock take session.")

    class Meta:
        ordering = ['-initiated_at']
        verbose_name = "Stock Take Session"
        verbose_name_plural = "Stock Take Sessions"

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class StockTakeItem(models.Model):
    session = models.ForeignKey(StockTakeSession, related_name='items', on_delete=models.CASCADE, help_text="The stock take session this item belongs to.")
    warehouse_product = models.ForeignKey(WarehouseProduct, on_delete=models.PROTECT, help_text="The system's Warehouse Product record this count refers to.")
    location_label_counted = models.CharField(max_length=50, blank=True, null=True, help_text="The physical location label where the item was found and counted (e.g., -01).")
    batch_number_counted = models.CharField(max_length=100, blank=True, null=True, help_text="The batch number observed on the physical product.")
    expiry_date_counted = models.DateField(blank=True, null=True, help_text="The expiry date observed on the physical product.")
    counted_quantity = models.PositiveIntegerField(help_text="The quantity of this item physically counted at this location/batch/expiry.")
    counted_at = models.DateTimeField(default=timezone.now, help_text="Timestamp when this specific item was counted/entered.")
    notes = models.TextField(blank=True, help_text="Optional notes specific to this counted item.")

    class Meta:
        ordering = ['session', 'warehouse_product__product__name', 'location_label_counted', 'batch_number_counted']
        verbose_name = "Stock Take Item"
        verbose_name_plural = "Stock Take Items"

    def __str__(self):
        return f"Count for {self.warehouse_product.product.sku} in {self.session.name}: {self.counted_quantity} units"

class StockDiscrepancy(models.Model):
    DISCREPANCY_TYPES = [
        ('MATCH', 'Match'),
        ('OVER', 'Over Count'),
        ('SHORT', 'Short Count'),
        ('NOT_IN_SYSTEM', 'Not in System'),
        ('NOT_COUNTED', 'Not Counted'),
        ('DETAIL_MISMATCH', 'Detail Mismatch'),
    ]
    session = models.ForeignKey(StockTakeSession, related_name='discrepancies', on_delete=models.CASCADE, help_text="The stock take session this discrepancy belongs to.")
    warehouse_product = models.ForeignKey(WarehouseProduct, on_delete=models.PROTECT, help_text="The system's Warehouse Product record this discrepancy primarily relates to.")
    system_inventory_batch_item = models.ForeignKey(InventoryBatchItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='discrepancies_found', help_text="The specific system batch item this discrepancy refers to (if applicable).")
    system_location_label = models.CharField(max_length=50, blank=True, null=True)
    system_batch_number = models.CharField(max_length=100, blank=True, null=True)
    system_expiry_date = models.DateField(blank=True, null=True)
    system_quantity = models.IntegerField(null=True, blank=True, help_text="Quantity as per system records at time of evaluation.")
    stock_take_item_reference = models.ForeignKey(StockTakeItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='discrepancies_identified')
    counted_location_label = models.CharField(max_length=50, blank=True, null=True)
    counted_batch_number = models.CharField(max_length=100, blank=True, null=True)
    counted_expiry_date = models.DateField(blank=True, null=True)
    counted_quantity = models.IntegerField(null=True, blank=True, help_text="Quantity as per physical count.")
    discrepancy_quantity = models.IntegerField(help_text="Difference: counted_quantity - system_quantity. Positive for over, negative for short.")
    discrepancy_type = models.CharField(max_length=20, choices=DISCREPANCY_TYPES, help_text="Type of discrepancy found.")
    notes = models.TextField(blank=True, help_text="Notes explaining the discrepancy or actions taken.")
    is_resolved = models.BooleanField(default=False, help_text="Has this discrepancy been addressed/resolved?")
    resolution_notes = models.TextField(blank=True, help_text="Notes on how the discrepancy was resolved.")
    evaluated_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='discrepancies_resolved')

    class Meta:
        ordering = ['session', 'discrepancy_type', 'warehouse_product__product__name']
        verbose_name = "Stock Discrepancy"
        verbose_name_plural = "Stock Discrepancies"

    def __str__(self):
        return f"{self.get_discrepancy_type_display()} for {self.warehouse_product.product.sku} in Session {self.session.id}"

    def save(self, *args, **kwargs):
        if self.counted_quantity is not None and self.system_quantity is not None:
            self.discrepancy_quantity = self.counted_quantity - self.system_quantity
        elif self.counted_quantity is not None:
            self.discrepancy_quantity = self.counted_quantity
        elif self.system_quantity is not None:
            self.discrepancy_quantity = -self.system_quantity
        else:
            self.discrepancy_quantity = 0
        super().save(*args, **kwargs)

class ErpStockCheckSession(models.Model):
    SESSION_STATUS_CHOICES = [
        ('PENDING_UPLOAD', 'Pending Upload'),
        ('PROCESSING', 'Processing Uploaded File'),
        ('UPLOAD_FAILED', 'Upload Failed'),
        ('PENDING_EVALUATION', 'Pending Evaluation'),
        ('EVALUATING', 'Evaluating Discrepancies'),
        ('EVALUATED', 'Evaluated'),
        ('CLOSED', 'Closed/Archived'),
    ]
    name = models.CharField(max_length=255, help_text="e.g., ERP Quantity Snapshot - 2025-05-16")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, help_text="Specific warehouse for this check, or leave blank for all applicable warehouses in the file.")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='erp_stock_checks_uploaded', on_delete=models.SET_NULL, null=True, help_text="User who uploaded the ERP data file.")
    uploaded_at = models.DateTimeField(default=timezone.now, help_text="Timestamp when the ERP data file was uploaded.")
    processed_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when the uploaded file was successfully processed into ErpStockCheckItems.")
    evaluated_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='erp_stock_checks_evaluated', on_delete=models.SET_NULL, null=True, blank=True, help_text="User who initiated the evaluation.")
    evaluated_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when the evaluation was completed.")
    status = models.CharField(max_length=30, choices=SESSION_STATUS_CHOICES, default='PENDING_UPLOAD', help_text="Current status of this ERP stock check session.")
    source_file_name = models.CharField(max_length=255, blank=True, null=True, help_text="Original name of the uploaded ERP file.")
    processing_notes = models.TextField(blank=True, help_text="Notes or errors from file processing stage.")
    evaluation_notes = models.TextField(blank=True, help_text="General notes from the evaluation stage.")

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = "ERP Stock Check Session"
        verbose_name_plural = "ERP Stock Check Sessions"

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class ErpStockCheckItem(models.Model):
    session = models.ForeignKey(ErpStockCheckSession, related_name='items', on_delete=models.CASCADE, help_text="The ERP stock check session this item belongs to.")
    warehouse_product = models.ForeignKey(WarehouseProduct, on_delete=models.PROTECT, help_text="The Warehouse Product record in your system.")
    erp_warehouse_name_raw = models.CharField(max_length=255, blank=True, null=True, help_text="Warehouse name as it appeared in the ERP file.")
    erp_product_sku_raw = models.CharField(max_length=100, blank=True, null=True, help_text="Product SKU as it appeared in the ERP file.")
    erp_product_name_raw = models.CharField(max_length=255, blank=True, null=True, help_text="Product Name as it appeared in the ERP file for reference.")
    erp_product_code_raw = models.CharField(max_length=100, blank=True, null=True, help_text="Warehouse Product Code as it appeared in the ERP file (if used for matching).")
    erp_quantity = models.IntegerField(help_text="Quantity of this product in the ERP system.")
    is_matched = models.BooleanField(default=False, help_text="Was this ERP item successfully matched to a WarehouseProduct in your system?")
    processing_comments = models.CharField(max_length=255, blank=True, null=True, help_text="Comments from the matching/processing stage, e.g., 'New item not in system'.")

    class Meta:
        ordering = ['session', 'warehouse_product__product__name']
        verbose_name = "ERP Stock Check Item"
        verbose_name_plural = "ERP Stock Check Items"
        unique_together = [['session', 'warehouse_product']]

    def __str__(self):
        return f"ERP Qty for {self.warehouse_product.product.sku} in {self.session.name}: {self.erp_quantity}"


class WarehouseProductDiscrepancy(models.Model):
    DISCREPANCY_TYPES = [
        ('MATCH', 'Match'),
        ('OVER_IN_SYSTEM', 'Over in Your System'),
        ('SHORT_IN_SYSTEM', 'Short in Your System'),
        ('NOT_IN_ERP', 'Not in ERP'),
        ('NOT_IN_SYSTEM', 'Not in Your System'),
    ]
    session = models.ForeignKey(ErpStockCheckSession, related_name='discrepancies', on_delete=models.CASCADE, help_text="The ERP stock check session this discrepancy belongs to.")
    warehouse_product = models.ForeignKey(WarehouseProduct, on_delete=models.PROTECT, null=True, blank=True, help_text="The Warehouse Product this discrepancy relates to.")
    erp_stock_check_item = models.ForeignKey(ErpStockCheckItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='discrepancy_record')
    erp_warehouse_name_for_unmatched = models.CharField(max_length=255, blank=True, null=True, help_text="ERP Warehouse Name (if item not matched to system WP)")
    erp_product_sku_for_unmatched = models.CharField(max_length=100, blank=True, null=True, help_text="ERP Product SKU (if item not matched to system WP)")
    erp_product_name_for_unmatched = models.CharField(max_length=255, blank=True, null=True, help_text="ERP Product Name (if item not matched to system WP)")
    system_quantity = models.IntegerField(help_text="Quantity as per your system (WarehouseProduct.quantity) at time of evaluation.")
    erp_quantity = models.IntegerField(null=True, blank=True, help_text="Quantity as per ERP system records from the uploaded file.")
    discrepancy_quantity = models.IntegerField(help_text="Difference: system_quantity - erp_quantity. Positive if your system has more, negative if less.")
    discrepancy_type = models.CharField(max_length=20, choices=DISCREPANCY_TYPES, help_text="Type of discrepancy found.")
    notes = models.TextField(blank=True, help_text="Notes explaining the discrepancy or actions to be taken.")
    is_resolved = models.BooleanField(default=False, help_text="Has this discrepancy been addressed/resolved?")
    resolution_notes = models.TextField(blank=True, help_text="Notes on how the discrepancy was resolved.")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='wp_discrepancies_resolved')

    class Meta:
        ordering = ['session', 'discrepancy_type', 'warehouse_product__product__name']
        verbose_name = "Warehouse Product Stock Discrepancy"
        verbose_name_plural = "Warehouse Product Stock Discrepancies"

    def __str__(self):
        return f"{self.get_discrepancy_type_display()} for {self.warehouse_product.product.sku} in Session {self.session.id}"

    def save(self, *args, **kwargs):
        if self.discrepancy_type == 'NOT_IN_ERP':
            self.discrepancy_quantity = self.system_quantity
            self.erp_quantity = 0
        elif self.discrepancy_type == 'NOT_IN_SYSTEM':
            self.discrepancy_quantity = -(self.erp_quantity if self.erp_quantity is not None else 0)
        elif self.system_quantity is not None and self.erp_quantity is not None:
            self.discrepancy_quantity = self.system_quantity - self.erp_quantity
        else:
            self.discrepancy_quantity = 0
        super().save(*args, **kwargs)


def process_order_allocation(order_instance):
    """
    Processes an order to suggest batch items for its order items.
    This function should be called after an order and its items are created/updated.
    """
    # MOVE THE IMPORT HERE:
    from .services import get_suggested_batch_for_order_item

    for item in order_instance.items.filter(status='PENDING_ALLOCATION'):
        quantity_to_allocate = item.quantity_ordered - item.quantity_allocated
        if quantity_to_allocate <= 0:
            if item.quantity_ordered == item.quantity_allocated and item.status == 'PENDING_ALLOCATION':
                item.status = 'ALLOCATED'
                item.suggested_batch_item = None
                item.suggested_batch_number_display = None
                item.suggested_batch_expiry_date_display = None
                item.save(update_fields=['status', 'suggested_batch_item', 'suggested_batch_number_display', 'suggested_batch_expiry_date_display'])
            continue

        suggested_batch = get_suggested_batch_for_order_item(item, quantity_to_allocate)

        if suggested_batch:
            item.suggested_batch_item = suggested_batch
            item.suggested_batch_number_display = suggested_batch.batch_number
            item.suggested_batch_expiry_date_display = suggested_batch.expiry_date
            item.status = 'ALLOCATED'
            item.save(update_fields=['suggested_batch_item', 'suggested_batch_number_display', 'suggested_batch_expiry_date_display', 'status'])
        else:
            item.status = 'ALLOCATION_FAILED'
            item.suggested_batch_item = None
            item.suggested_batch_number_display = "N/A" # Or None, depending on desired display
            item.suggested_batch_expiry_date_display = None
            item.save(update_fields=['status', 'suggested_batch_item', 'suggested_batch_number_display', 'suggested_batch_expiry_date_display'])

class PackagingMaterial(models.Model):
    name = models.CharField(max_length=150, unique=True, help_text="Name of the packaging material (e.g., Foam Box A1, Gel Pack 500g).")
    material_code = models.CharField(max_length=50, unique=True, blank=True, null=True, help_text="Internal SKU or code for the material.")
    description = models.TextField(blank=True, null=True)

    length_cm = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    width_cm = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    height_cm = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    current_stock = models.PositiveIntegerField(default=0, help_text="Current available stock quantity.")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, blank=True, null=True) # If you have a Supplier model
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    reorder_level = models.PositiveIntegerField(default=10, help_text="Stock level at which to reorder.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} (Code: {self.material_code or 'N/A'}) - Stock: {self.current_stock}"

    class Meta:
        verbose_name = "Packaging Material"
        verbose_name_plural = "Packaging Materials"
        ordering = ['name']
