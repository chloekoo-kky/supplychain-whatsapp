# Generated by Django 3.2.25 on 2025-05-13 05:59

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0007_auto_20250512_1911'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehouseproduct',
            name='expiry_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]
