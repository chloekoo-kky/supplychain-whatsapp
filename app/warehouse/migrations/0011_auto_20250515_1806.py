# Generated by Django 3.2.25 on 2025-05-15 10:06

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0009_stockdiscrepancy'),
        ('warehouse', '0010_purchaseorder_created_at'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='warehouseproduct',
            options={'ordering': ['warehouse__name', 'product__name']},
        ),
        migrations.AddField(
            model_name='warehouseproduct',
            name='code',
            field=models.CharField(blank=True, help_text='Easy to remember, human-readable code for this product in this warehouse (e.g., WH1-PROD-001).', max_length=50, null=True),
        ),
        migrations.AlterField(
            model_name='warehouseproduct',
            name='supplier',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='inventory.supplier'),
        ),
        migrations.AlterUniqueTogether(
            name='warehouseproduct',
            unique_together={('warehouse', 'product'), ('warehouse', 'code')},
        ),
    ]
