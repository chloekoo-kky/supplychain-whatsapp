# Generated by Django 3.2.25 on 2025-04-26 13:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0002_auto_20250424_0940'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='eta',
            field=models.DateField(blank=True, null=True),
        ),
    ]
