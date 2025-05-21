# operation/migrations/0004_alter_parcel_code_constraints.py
from django.db import migrations, models
# Adjust this import if your generate_parcel_code function is elsewhere
from operation.models import generate_parcel_code

class Migration(migrations.Migration):

    dependencies = [
        ('operation', '0003_populate_parcel_codes'), # Depends on the data population step
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='parcel_code',
            # The default callable is for new instances created via the ORM *after* this migration.
            # The NOT NULL constraint will be applied. Ensure 0003_populate_parcel_codes
            # actually filled all previously NULL parcel_codes.
            field=models.CharField(max_length=50, unique=True, default=generate_parcel_code),
            # CharField is NOT NULL by default unless null=True is explicitly set.
            # If populate_parcel_codes might have missed some, this AlterField could fail.
        ),
    ]
