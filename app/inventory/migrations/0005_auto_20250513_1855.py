# Generated by Django 3.2.25 on 2025-05-13 10:55

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0004_auto_20250512_1911'),
    ]

    operations = [
        migrations.AlterField(
            model_name='inventorybatchitem',
            name='batch_number',
            field=models.CharField(max_length=100, null=True, verbose_name='Batch Number'),
        ),
        migrations.AlterField(
            model_name='inventorybatchitem',
            name='expiry_date',
            field=models.DateField(null=True, verbose_name='Expiry Date'),
        ),
    ]
