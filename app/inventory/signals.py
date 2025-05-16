# inventory/signals.py (you'd need to create this file and import it in apps.py)
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum
from .models import InventoryBatchItem, WarehouseProduct

@receiver(post_save, sender=InventoryBatchItem)
@receiver(post_delete, sender=InventoryBatchItem)
def update_warehouse_product_total_quantity(sender, instance, **kwargs):
    warehouse_product = instance.warehouse_product
    if warehouse_product:
        # Recalculate the total quantity for the parent WarehouseProduct
        new_total = InventoryBatchItem.objects.filter(
            warehouse_product=warehouse_product
        ).aggregate(total=Sum('quantity'))['total'] or 0

        # Update without triggering signals again if WarehouseProduct had its own save method
        WarehouseProduct.objects.filter(pk=warehouse_product.pk).update(total_quantity=new_total)
        # Or if you add a 'total_quantity' field to WarehouseProduct model:
        # warehouse_product.total_quantity = new_total
        # warehouse_product.save(update_fields=['total_quantity']) # if you have a field and want to avoid recursion
