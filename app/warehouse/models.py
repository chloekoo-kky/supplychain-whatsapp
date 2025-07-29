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
    photo = models.ImageField(
        upload_to='product_photos/warehouse/',
        blank=True,
        null=True,
        help_text="Warehouse-specific product image"
    )
    length = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Length in cm"
    )
    width = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Width in cm"
    )
    height = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Height in cm"
    )
    max_ship_qty_a = models.PositiveIntegerField(
        null=True, blank=True, help_text="Max quantity to ship for condition A"
    )
    max_ship_qty_b = models.PositiveIntegerField(
        null=True, blank=True, help_text="Max quantity to ship for condition B"
    )

    selling_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Warehouse-specific selling price"
    )

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
        """Calculates the total quantity of this product from active POs."""
        return sum(item.balance_quantity for item in self.incoming_po_items)

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

class PurchaseOrder(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('WAITING_INVOICE', 'Waiting Invoice'),
        ('PAYMENT_MADE', 'Payment Made'),
        ('PARTIALLY_DELIVERED', 'Partially Delivered'),
        ('DELIVERED', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.CharField(max_length=50, primary_key=True, unique=True, editable=False)


    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.PROTECT)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='DRAFT')
    eta = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    # 2. Override the save() method to generate the custom ID
    def save(self, *args, **kwargs):
        # Only generate a new ID if this is a new PO (it has no ID yet).
        if not self.id:
            # Use a database transaction to prevent race conditions.
            with transaction.atomic():
                # Get the sequence object, creating it if it doesn't exist.
                # The select_for_update() locks the row until this transaction is complete.
                sequence_obj, created = PurchaseOrderSequence.objects.select_for_update().get_or_create(pk=1)

                # Increment and save the new last number
                new_number = sequence_obj.last_number + 1
                sequence_obj.last_number = new_number
                sequence_obj.save()

            # Format the new ID (e.g., 'MBA' + '001')
            supplier_code = self.supplier.code.upper()
            padded_number = str(new_number).zfill(3) # Zero-pads the number to 3 digits
            self.id = f"{supplier_code}{padded_number}"

        super().save(*args, **kwargs) # Call the original save method


    def __str__(self):
        return f"PO {self.id} - {self.supplier.name}"

    @property
    def total_amount(self):
        return sum(item.total_price for item in self.items.all())

    @property
    def is_receivable(self):
        """Returns True if the PO is in a state where items can be received."""
        return self.status in ['PAYMENT_MADE', 'PARTIALLY_DELIVERED']

    @property
    def is_fully_received(self):
        """Returns True if all items on this PO have been fully received."""
        # This checks if the balance quantity is zero for every single item on the PO.
        return all(item.balance_quantity == 0 for item in self.items.all())

class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, related_name='items', on_delete=models.CASCADE)
    item = models.ForeignKey(WarehouseProduct, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    received_quantity = models.PositiveIntegerField(default=0)

    @property
    def total_price(self):
        return self.quantity * self.price

    @property
    def balance_quantity(self):
        return self.quantity - self.received_quantity

class PurchaseOrderStatusLog(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='status_logs')
    status = models.CharField(max_length=50, choices=PurchaseOrder.STATUS_CHOICES)
    timestamp = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey('core.User', on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f'{self.purchase_order.id}: {self.get_status_display()} at {self.timestamp}'


class PurchaseOrderReceiptItem(models.Model):
    # CORRECTED: This now correctly links to the new status log model
    status_log = models.ForeignKey(PurchaseOrderStatusLog, on_delete=models.CASCADE, related_name='received_items', null=True)
    po_item = models.ForeignKey(PurchaseOrderItem, on_delete=models.CASCADE)
    quantity_received_this_time = models.PositiveIntegerField()


class PurchaseOrderSequence(models.Model):
    last_number = models.PositiveIntegerField(default=0)
