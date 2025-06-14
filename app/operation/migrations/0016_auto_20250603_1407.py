# Generated by Django 3.2.25 on 2025-06-03 06:07

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('operation', '0015_auto_20250602_1829'),
    ]

    operations = [
        migrations.AddField(
            model_name='parcel',
            name='dimensional_weight_kg',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True, verbose_name='Dimensional Weight (kg)'),
        ),
        migrations.AddField(
            model_name='parcel',
            name='height',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True, verbose_name='Height (cm)'),
        ),
        migrations.AddField(
            model_name='parcel',
            name='length',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True, verbose_name='Length (cm)'),
        ),
        migrations.AddField(
            model_name='parcel',
            name='width',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True, verbose_name='Width (cm)'),
        ),
    ]
