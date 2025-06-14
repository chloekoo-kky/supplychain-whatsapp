# Generated by Django 3.2.25 on 2025-06-03 11:31

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('operation', '0018_auto_20250603_1925'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomsDeclaration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.TextField(help_text='Description of the goods for customs purposes.')),
                ('hs_code', models.CharField(help_text='Harmonized System (HS) code.', max_length=20)),
                ('shipment_type', models.CharField(choices=[('MIX', 'Cold Chain + Ambient'), ('AMBIENT', 'Ambient Only'), ('COLD_CHAIN', 'Cold Chain Only')], default='ANY', help_text='Specify if this declaration is for ambient, cold chain, or any shipment type.', max_length=20)),
                ('notes', models.TextField(blank=True, help_text='Optional notes.', null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('courier_company', models.ForeignKey(blank=True, help_text='Specific courier this declaration applies to, or blank if generic.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customs_declarations', to='operation.couriercompany')),
            ],
            options={
                'verbose_name': 'Customs Declaration',
                'verbose_name_plural': 'Customs Declarations',
                'ordering': ['courier_company__name', 'shipment_type', 'description'],
            },
        ),
        migrations.AddConstraint(
            model_name='customsdeclaration',
            constraint=models.UniqueConstraint(condition=models.Q(('courier_company__isnull', False)), fields=('description', 'hs_code', 'courier_company', 'shipment_type'), name='unique_decl_with_courier_desc_hs_type'),
        ),
        migrations.AddConstraint(
            model_name='customsdeclaration',
            constraint=models.UniqueConstraint(condition=models.Q(('courier_company__isnull', True)), fields=('description', 'hs_code', 'shipment_type'), name='unique_decl_without_courier_desc_hs_type'),
        ),
    ]
