# inventory/models.py
from django.db import models
from django.utils import timezone
from django.conf import settings

from operation.models import Order
from warehouse.models import Warehouse, WarehouseProduct

class Supplier(models.Model):
    name = models.CharField(max_length=100, null=True)
    code = models.CharField(max_length=20, unique=True, null=True)
    address = models.TextField(null=True, blank=True) # Allow blank for address
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

    # increase_stock and decrease_stock methods are usually better handled
    # at the WarehouseProduct level or via StockTransactions, not directly on Product.
    # Removing them here unless they have a specific global meaning.


class StockTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('IN', 'Stock In (PO Received)'),
        ('OUT', 'Stock Out (Sales Order)'),
        ('RETURN', 'Return In'),
        ('CANCEL', 'Cancel / Restore'),
        ('ADJUST', 'Manual Adjustment'),
        ('PO_ITEM_DEL_ADJ', 'PO Item Deletion Adjustment'), # Added for clarity
    ]

    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.CASCADE)
    # Changed to string reference to avoid circular import if WarehouseProduct is in warehouse.models
    warehouse_product = models.ForeignKey('warehouse.WarehouseProduct', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES) # Increased max_length
    quantity = models.IntegerField()
    reference_note = models.CharField(max_length=255, blank=True)
    related_po = models.ForeignKey('warehouse.PurchaseOrder', null=True, blank=True, on_delete=models.SET_NULL)
    related_order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL) # Make sure Order is imported
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_transaction_type_display()}] {self.product.name} x {self.quantity} @ {self.warehouse.name}"


class InventoryBatchItem(models.Model):
    # Changed to string reference to avoid circular import issue
    warehouse_product = models.ForeignKey('warehouse.WarehouseProduct', on_delete=models.CASCADE, related_name="batches")
    batch_number = models.CharField(max_length=100, null=True, blank=False, verbose_name="Batch Number") # Batch number is likely required

    # New field for location label
    location_label = models.CharField(max_length=50, null=True, blank=True, verbose_name="Location Label (e.g., Box/Shelf ID)")

    expiry_date = models.DateField(null=True, blank=True, verbose_name="Expiry Date") # Allow blank for expiry
    quantity = models.PositiveIntegerField(default=0, verbose_name="Quantity in this Batch/Location")
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Cost Price (per unit)")
    date_received = models.DateField(default=timezone.now, verbose_name="Date Received")
    last_modified = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        batch_display = self.batch_number if self.batch_number else "NO_BATCH_ID"
        location_display = f" (Loc: {self.location_label})" if self.location_label else ""
        wp_display = str(self.warehouse_product) if self.warehouse_product else "N/A WarehouseProduct"
        return f"{wp_display} - Batch: {batch_display}{location_display} (Qty: {self.quantity})"

    class Meta:
        # Updated unique_together constraint
        unique_together = [['warehouse_product', 'batch_number', 'location_label']]
        ordering = ['warehouse_product__product__name', 'warehouse_product__warehouse__name', 'batch_number', 'location_label', 'expiry_date']
        verbose_name = "Inventory Batch Item"
        verbose_name_plural = "Inventory Batch Items"

    def save(self, *args, **kwargs):
        # If batch_number is empty string, treat as null for uniqueness if your DB allows (PostgreSQL does)
        # Or enforce that batch_number cannot be an empty string if it's part of unique key and not nullable.
        # For now, assuming batch_number is required (blank=False).
        # If location_label is an empty string, convert it to None to ensure uniqueness works correctly
        # if the database treats NULLs differently from empty strings in unique constraints.
        if self.location_label == '':
            self.location_label = None

        super().save(*args, **kwargs)

    @property
    def expiry_status_display(self): # Keep this useful property for frontend
        if not self.expiry_date:
            return None
        today = timezone.now().date()
        delta = self.expiry_date - today
        if delta.days < 0:
            return "Expired"
        elif 0 <= delta.days <= 180: # Approx 6 months
            return "≤6m"
        return None

class StockTakeSession(models.Model):
    """
    Represents a single stock-taking session/event.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending Operator Input'),
        ('COMPLETED_BY_OPERATOR', 'Completed'), # Operator has finished inputting
        ('EVALUATED', 'Evaluated by Superuser'),
        ('CLOSED', 'Closed/Archived'),
    ]

    name = models.CharField(max_length=255, help_text="e.g., Full Stock Take - 2025-05-15 - Main Warehouse")
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT, # Prevent deleting a warehouse if stock takes depend on it
        help_text="The warehouse where the stock take was performed."
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='stock_takes_initiated',
        on_delete=models.SET_NULL,
        null=True,
        blank=True, # Can be system-initiated or by any user with permission
        help_text="User who started or is responsible for this stock take session."
    )
    initiated_at = models.DateTimeField(
        default=timezone.now,
        help_text="Timestamp when the stock take session was created/initiated."
    )
    completed_by_operator_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the warehouse operator marked their counting as complete."
    )
    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='stock_takes_evaluated',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Superuser who evaluated this stock take."
    )
    evaluated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the stock take was evaluated."
    )
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default='PENDING',
        help_text="Current status of the stock take session."
    )
    notes = models.TextField(blank=True, help_text="General notes for this stock take session.")

    class Meta:
        ordering = ['-initiated_at']
        verbose_name = "Stock Take Session"
        verbose_name_plural = "Stock Take Sessions"

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class StockTakeItem(models.Model):
    """
    Represents a single item entry recorded during a stock take session.
    This captures what the operator physically observed and counted.
    """
    session = models.ForeignKey(
        StockTakeSession,
        related_name='items',
        on_delete=models.CASCADE, # If session is deleted, its items are deleted
        help_text="The stock take session this item belongs to."
    )
    # Operator selects the WarehouseProduct they believe they are counting.
    # This links the count to a known SKU and product in a specific warehouse.
    warehouse_product = models.ForeignKey(
        WarehouseProduct,
        on_delete=models.PROTECT, # Protect WarehouseProduct from deletion if part of a stock take
        help_text="The system's Warehouse Product record this count refers to."
    )
    # Fields below capture the *actual observed* details by the operator
    location_label_counted = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="The physical location label where the item was found and counted (e.g., -01)."
    )
    batch_number_counted = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="The batch number observed on the physical product."
    )
    expiry_date_counted = models.DateField(
        blank=True,
        null=True,
        help_text="The expiry date observed on the physical product."
    )
    counted_quantity = models.PositiveIntegerField(
        help_text="The quantity of this item physically counted at this location/batch/expiry."
    )
    # Timestamps and notes for the individual item entry
    counted_at = models.DateTimeField(
        default=timezone.now, # Or auto_now_add=True if entry is created immediately
        help_text="Timestamp when this specific item was counted/entered."
    )
    notes = models.TextField(blank=True, help_text="Optional notes specific to this counted item.")

    class Meta:
        ordering = ['session', 'warehouse_product__product__name', 'location_label_counted', 'batch_number_counted']
        verbose_name = "Stock Take Item"
        verbose_name_plural = "Stock Take Items"
        # Consider unique constraints if needed, e.g., unique per session, warehouse_product, location, batch, expiry?
        # For now, allowing multiple entries for the same combo, sum them up later or enforce via form.
        # unique_together = [['session', 'warehouse_product', 'location_label_counted', 'batch_number_counted', 'expiry_date_counted']]


    def __str__(self):
        return f"Count for {self.warehouse_product.product.sku} in {self.session.name}: {self.counted_quantity} units"

class StockDiscrepancy(models.Model):
    """
    Records a discrepancy found during the evaluation of a stock take session.
    Compares a counted item (or aggregation of counts) with system records.
    """
    DISCREPANCY_TYPES = [
        ('MATCH', 'Match'), # System and Counted quantities match for the given details
        ('OVER', 'Over Count'), # Counted quantity is more than system quantity
        ('SHORT', 'Short Count'), # Counted quantity is less than system quantity
        ('NOT_IN_SYSTEM', 'Not in System'), # Item counted but no matching system record (batch/location)
        ('NOT_COUNTED', 'Not Counted'), # Item in system (batch/location) but not found in stock take items
        ('DETAIL_MISMATCH', 'Detail Mismatch'), # e.g. Qty matches but batch/expiry/location differs
    ]

    session = models.ForeignKey(
        StockTakeSession,
        related_name='discrepancies',
        on_delete=models.CASCADE,
        help_text="The stock take session this discrepancy belongs to."
    )
    warehouse_product = models.ForeignKey(
        WarehouseProduct,
        on_delete=models.PROTECT,
        help_text="The system's Warehouse Product record this discrepancy primarily relates to."
    )

    # Details from the System (InventoryBatchItem)
    system_inventory_batch_item = models.ForeignKey(
        InventoryBatchItem,
        on_delete=models.SET_NULL, # Keep discrepancy record even if system batch is deleted/merged
        null=True, blank=True,
        related_name='discrepancies_found',
        help_text="The specific system batch item this discrepancy refers to (if applicable)."
    )
    system_location_label = models.CharField(max_length=50, blank=True, null=True)
    system_batch_number = models.CharField(max_length=100, blank=True, null=True)
    system_expiry_date = models.DateField(blank=True, null=True)
    system_quantity = models.IntegerField(null=True, blank=True, help_text="Quantity as per system records at time of evaluation.")

    # Details from the Stock Take (StockTakeItem or aggregation)
    stock_take_item_reference = models.ForeignKey( # Optional: link to a specific counted item if discrepancy is 1-to-1
        StockTakeItem,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='discrepancies_identified'
    )
    counted_location_label = models.CharField(max_length=50, blank=True, null=True)
    counted_batch_number = models.CharField(max_length=100, blank=True, null=True)
    counted_expiry_date = models.DateField(blank=True, null=True)
    counted_quantity = models.IntegerField(null=True, blank=True, help_text="Quantity as per physical count.")

    # Discrepancy Details
    discrepancy_quantity = models.IntegerField(
        help_text="Difference: counted_quantity - system_quantity. Positive for over, negative for short."
    )
    discrepancy_type = models.CharField(
        max_length=20,
        choices=DISCREPANCY_TYPES,
        help_text="Type of discrepancy found."
    )

    notes = models.TextField(blank=True, help_text="Notes explaining the discrepancy or actions taken.")
    is_resolved = models.BooleanField(default=False, help_text="Has this discrepancy been addressed/resolved?")
    resolution_notes = models.TextField(blank=True, help_text="Notes on how the discrepancy was resolved.")

    evaluated_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='discrepancies_resolved'
    )


    class Meta:
        ordering = ['session', 'discrepancy_type', 'warehouse_product__product__name']
        verbose_name = "Stock Discrepancy"
        verbose_name_plural = "Stock Discrepancies"

    def __str__(self):
        return f"{self.get_discrepancy_type_display()} for {self.warehouse_product.product.sku} in Session {self.session.id}"

    def save(self, *args, **kwargs):
        if self.counted_quantity is not None and self.system_quantity is not None:
            self.discrepancy_quantity = self.counted_quantity - self.system_quantity
        elif self.counted_quantity is not None: # Not in system
            self.discrepancy_quantity = self.counted_quantity
        elif self.system_quantity is not None: # Not counted
            self.discrepancy_quantity = -self.system_quantity
        else:
            self.discrepancy_quantity = 0

        super().save(*args, **kwargs)

class ErpStockCheckSession(models.Model):
    """
    Represents a session for checking WarehouseProduct quantities against an ERP export.
    """
    SESSION_STATUS_CHOICES = [
        ('PENDING_UPLOAD', 'Pending Upload'),
        ('PROCESSING', 'Processing Uploaded File'),
        ('UPLOAD_FAILED', 'Upload Failed'),
        ('PENDING_EVALUATION', 'Pending Evaluation'), # File uploaded, items created, ready for comparison
        ('EVALUATING', 'Evaluating Discrepancies'),
        ('EVALUATED', 'Evaluated'),
        ('CLOSED', 'Closed/Archived'),
    ]

    name = models.CharField(
        max_length=255,
        help_text="e.g., ERP Quantity Snapshot - 2025-05-16"
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.SET_NULL, # Or PROTECT, depends on if you want to keep sessions for deleted warehouses
        null=True, blank=True, # Can be for a specific warehouse or all (if null)
        help_text="Specific warehouse for this check, or leave blank for all applicable warehouses in the file."
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='erp_stock_checks_uploaded',
        on_delete=models.SET_NULL,
        null=True,
        help_text="User who uploaded the ERP data file."
    )
    uploaded_at = models.DateTimeField(
        default=timezone.now,
        help_text="Timestamp when the ERP data file was uploaded."
    )
    processed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp when the uploaded file was successfully processed into ErpStockCheckItems."
    )
    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='erp_stock_checks_evaluated',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text="User who initiated the evaluation."
    )
    evaluated_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp when the evaluation was completed."
    )
    status = models.CharField(
        max_length=30,
        choices=SESSION_STATUS_CHOICES,
        default='PENDING_UPLOAD',
        help_text="Current status of this ERP stock check session."
    )
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
    """
    Represents a single WarehouseProduct's quantity as reported by the ERP system
    for a specific ErpStockCheckSession.
    """
    session = models.ForeignKey(
        ErpStockCheckSession,
        related_name='items',
        on_delete=models.CASCADE,
        help_text="The ERP stock check session this item belongs to."
    )
    warehouse_product = models.ForeignKey(
        WarehouseProduct,
        on_delete=models.PROTECT, # Critical to not delete WP if it's part of a check
        help_text="The Warehouse Product record in your system."
    )
    # Identifying information from the Excel (stored for reference/audit)
    erp_warehouse_name_raw = models.CharField(max_length=255, blank=True, null=True, help_text="Warehouse name as it appeared in the ERP file.")
    erp_product_sku_raw = models.CharField(max_length=100, blank=True, null=True, help_text="Product SKU as it appeared in the ERP file.")
    erp_product_code_raw = models.CharField(max_length=100, blank=True, null=True, help_text="Warehouse Product Code as it appeared in the ERP file (if used for matching).")

    erp_quantity = models.IntegerField( # Assuming ERP quantities are integers
        help_text="Quantity of this product in the ERP system."
    )
    is_matched = models.BooleanField(default=False, help_text="Was this ERP item successfully matched to a WarehouseProduct in your system?")
    processing_comments = models.CharField(max_length=255, blank=True, null=True, help_text="Comments from the matching/processing stage, e.g., 'New item not in system'.")

    class Meta:
        ordering = ['session', 'warehouse_product__product__name']
        verbose_name = "ERP Stock Check Item"
        verbose_name_plural = "ERP Stock Check Items"
        unique_together = [['session', 'warehouse_product']] # Only one ERP quantity per WP per session

    def __str__(self):
        return f"ERP Qty for {self.warehouse_product.product.sku} in {self.session.name}: {self.erp_quantity}"


class WarehouseProductDiscrepancy(models.Model):
    """
    Records a discrepancy found when comparing WarehouseProduct.quantity (your system)
    with ErpStockCheckItem.erp_quantity (ERP system).
    """
    DISCREPANCY_TYPES = [
        ('MATCH', 'Match'),
        ('OVER_IN_SYSTEM', 'Over in Your System'), # Your system has more than ERP
        ('SHORT_IN_SYSTEM', 'Short in Your System'),  # Your system has less than ERP
        ('NOT_IN_ERP', 'Not in ERP'),       # WarehouseProduct exists in your system but not in ERP upload for that warehouse
        ('NOT_IN_SYSTEM', 'Not in Your System'), # Item in ERP upload but no matching WarehouseProduct found (less common if you pre-validate WP)
    ]

    session = models.ForeignKey(
        ErpStockCheckSession,
        related_name='discrepancies',
        on_delete=models.CASCADE,
        help_text="The ERP stock check session this discrepancy belongs to."
    )
    warehouse_product = models.ForeignKey(
        WarehouseProduct,
        on_delete=models.PROTECT, # Keep discrepancy record even if WP is later deleted (though ideally it shouldn't be if there's a discrepancy)
        help_text="The Warehouse Product this discrepancy relates to."
    )
    # Optional link back to the specific ErpStockCheckItem that triggered this (if applicable)
    erp_stock_check_item = models.OneToOneField(
        ErpStockCheckItem,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='discrepancy_record'
    )

    system_quantity = models.IntegerField(
        help_text="Quantity as per your system (WarehouseProduct.quantity) at time of evaluation."
    )
    erp_quantity = models.IntegerField(
        null=True, blank=True, # Can be null if type is NOT_IN_ERP
        help_text="Quantity as per ERP system records from the uploaded file."
    )
    discrepancy_quantity = models.IntegerField(
        help_text="Difference: system_quantity - erp_quantity. Positive if your system has more, negative if less."
    )
    discrepancy_type = models.CharField(
        max_length=20,
        choices=DISCREPANCY_TYPES,
        help_text="Type of discrepancy found."
    )

    notes = models.TextField(blank=True, help_text="Notes explaining the discrepancy or actions to be taken.")
    is_resolved = models.BooleanField(default=False, help_text="Has this discrepancy been addressed/resolved?")
    resolution_notes = models.TextField(blank=True, help_text="Notes on how the discrepancy was resolved.")
    created_at = models.DateTimeField(auto_now_add=True) # When the discrepancy record was created (i.e., evaluation time)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='wp_discrepancies_resolved'
    )

    class Meta:
        ordering = ['session', 'discrepancy_type', 'warehouse_product__product__name']
        verbose_name = "Warehouse Product Stock Discrepancy"
        verbose_name_plural = "Warehouse Product Stock Discrepancies"

    def __str__(self):
        return f"{self.get_discrepancy_type_display()} for {self.warehouse_product.product.sku} in Session {self.session.id}"

    def save(self, *args, **kwargs):
        # Auto-calculate discrepancy_quantity
        if self.discrepancy_type == 'NOT_IN_ERP':
            self.discrepancy_quantity = self.system_quantity # System has items, ERP has 0
            self.erp_quantity = 0 # Explicitly set for clarity
        elif self.discrepancy_type == 'NOT_IN_SYSTEM':
            self.discrepancy_quantity = -(self.erp_quantity if self.erp_quantity is not None else 0) # ERP has items, system has 0
        elif self.system_quantity is not None and self.erp_quantity is not None:
            self.discrepancy_quantity = self.system_quantity - self.erp_quantity
        else:
            # This case should ideally be prevented by how discrepancy_type is set
            self.discrepancy_quantity = 0

        super().save(*args, **kwargs)
