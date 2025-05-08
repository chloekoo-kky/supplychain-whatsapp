from django.db import models

class Order(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft Order'),
        ('NEW', 'New Order'),
        ('PARTIAL', 'Partially Shipped'),
        ('COMPLETED', 'Fully Completed'),
        ('BILLED', 'Invoice Updated'),
        ('CANCELLED', 'Cancelled'),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    customer = models.TextField(blank=True, null=True)
    inventory_updated = models.BooleanField(default=False)
    order_date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"Order#{self.id} - {self.customer} - {self.status}"

    @property
    def all_parcels(self):
        return self.parcels.all()

    @property
    def all_order_items(self):
        return self.items.all()

    def apply_inventory_on_shipping(self, quantity_factor=-1):
        for parcel in self.parcels.all():
            product = parcel.product
            product.quantity += parcel.quantity * quantity_factor
            product.save()

            'warehoue.InventoryTransaction'.objects.create(
                product=product,
                quantity=parcel.quantity * quantity_factor,
                transaction_type='OUT' if quantity_factor < 0 else 'RETURN',
                related_order=self
            )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = Order.objects.get(pk=self.pk)
            super().save(*args, **kwargs)
            if previous.status != 'COMPLETED' and self.status == 'COMPLETED':
                self.apply_inventory_on_shipping(quantity_factor=-1)
                self.inventory_updated = True
                self.save()
            elif previous.status != 'CANCELLED' and self.status == 'CANCELLED':
                self.apply_inventory_on_shipping(quantity_factor=1)
                self.inventory_updated = True
                self.save()
        else:
            super().save(*args, **kwargs)

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"

class Parcels(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='parcels')
    product = models.ForeignKey('inventory.Product', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    tracking_number = models.CharField(max_length=255)
    courier_name = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.tracking_number} - {self.product.name}"
