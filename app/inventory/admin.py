
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path
from django import forms


from django.http import JsonResponse, HttpResponse


from .models import Product, Supplier, StockTransaction

import csv
import chardet


class CsvImportForm(forms.Form):
    csv_upload = forms.FileField()

# ===== 新版 ProductAdmin (upload_csv 可以创建新 SKU) =====
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    change_list_template = "admin/inventory/product/changelist.html"

    list_display = ('sku', 'name', 'price', 'created_date')
    search_fields = ('sku', 'name')
    list_filter = ('supplier',)
    ordering = ('-created_date',)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('upload-csv/', self.admin_site.admin_view(self.upload_csv), name='inventory_product_upload_csv'),
            path('download-csv-template/', self.admin_site.admin_view(self.download_csv_template), name='inventory_product_download_csv_template'),
        ]
        return custom_urls + urls

    def upload_csv(self, request):
        if request.method == "POST":
            csv_file = request.FILES.get("csv_upload")
            if not csv_file:
                self.message_user(request, "No file selected.", level=messages.ERROR)
                return redirect("..")

            raw_data = csv_file.read()
            result = chardet.detect(raw_data)
            encoding = result['encoding']
            decoded_file = raw_data.decode(encoding).splitlines()
            reader = csv.DictReader(decoded_file)

            created_count = 0
            updated_count = 0
            errors = []

            for line_num, row in enumerate(reader, start=2):
                sku = row.get('sku')
                name = row.get('name')
                price = row.get('price')
                supplier_name = row.get('supplier')

                if not sku:
                    errors.append(f"Row {line_num}: SKU is required.")
                    continue

                supplier = Supplier.objects.filter(name=supplier_name).first() if supplier_name else None

                product = Product.objects.filter(sku=sku).first()

                try:
                    price = float(price) if price else 0
                except ValueError:
                    errors.append(f"Row {line_num}: Invalid price format.")
                    continue

                if product:
                    # 更新 Product
                    product.name = name or product.name
                    product.price = price
                    product.supplier = supplier
                    product.save()
                    updated_count += 1
                else:
                    # 新建 Product
                    Product.objects.create(
                        sku=sku,
                        name=name or f"Unnamed Product ({sku})",
                        price=price,
                        supplier=supplier
                    )
                    created_count += 1

            if errors:
                for error in errors:
                    self.message_user(request, error, level=messages.WARNING)

            self.message_user(
                request,
                f"Upload completed: {created_count} created, {updated_count} updated.",
                level=messages.SUCCESS
            )
            return redirect("..")

        self.message_user(request, "Only POST method is allowed.", level=messages.ERROR)
        return redirect("..")

    def download_csv_template(self, request):
        response = HttpResponse(
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="product_upload_template.csv"'},
        )
        writer = csv.writer(response)
        writer.writerow(['sku', 'name', 'price', 'supplier'])
        writer.writerow(['SKU123', 'Sample Product', '9.99', 'Supplier Name'])
        return response

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')

@admin.register(StockTransaction)
class StockTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'product', 'warehouse', 'transaction_type',
        'quantity', 'reference_note', 'related_po', 'related_order', 'created_at'
    )
    list_filter = (
        'transaction_type', 'warehouse',
        ('created_at', admin.DateFieldListFilter),
    )
    search_fields = (
        'product__name', 'product__sku',
        'warehouse__name', 'reference_note',
        'related_po__id', 'related_order__id'
    )
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)

    fieldsets = (
        (None, {
            'fields': (
                'warehouse', 'warehouse_product', 'product',
                'transaction_type', 'quantity', 'reference_note',
                'related_po', 'related_order', 'created_at'
            )
        }),
    )
