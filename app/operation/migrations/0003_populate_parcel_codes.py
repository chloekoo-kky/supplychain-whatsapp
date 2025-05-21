# operation/migrations/0003_populate_parcel_codes.py
from django.db import migrations
# Adjust this import if your generate_parcel_code function is elsewhere
from operation.models import generate_parcel_code

def forwards_func(apps, schema_editor):
    Order = apps.get_model('operation', 'Order')
    # Ensure generate_parcel_code can be imported and used here.
    # It should be a function that doesn't rely on the request or other runtime context
    # that wouldn't be available in a migration.

    orders_to_update = Order.objects.filter(parcel_code__isnull=True)
    for order in orders_to_update:
        # If generate_parcel_code() might produce duplicates by chance and the DB isn't enforcing uniqueness yet:
        # You might need to ensure uniqueness programmatically here if `generate_parcel_code` isn't robust enough
        # by itself for a bulk operation, e.g., by checking if a code already exists in the batch being processed
        # or by having generate_parcel_code query the DB (though be careful with historical models in `apps.get_model`).
        # For simplicity now, assuming generate_parcel_code is good at making unique values:
        order.parcel_code = generate_parcel_code()
        order.save(update_fields=['parcel_code'])

def backwards_func(apps, schema_editor):
    # If you want to make this migration reversible,
    # you could set parcel_code back to NULL for these rows.
    Order = apps.get_model('operation', 'Order')
    Order.objects.all().update(parcel_code=None) # Or be more specific if needed

class Migration(migrations.Migration):

    dependencies = [
        ('operation', '0002_auto_20250518_1815'), # Adjust to your exact previous migration filename
    ]

    operations = [
        migrations.RunPython(forwards_func, reverse_code=backwards_func),
    ]
