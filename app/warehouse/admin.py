# ===== warehouse/admin.py =====
import csv
import chardet
from datetime import datetime

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponse
from django.urls import path
from django.shortcuts import redirect

from warehouse.models import Warehouse, WarehouseProduct, PurchaseOrder, PurchaseOrderItem
from inventory.models import Product, Supplier


class WarehouseProductCsvImportForm(forms.Form):
    csv_upload = forms.FileField()

# ===== 新版 WarehouseProductAdmin (只根据现有 SKU 更新仓库) =====
@admin.register(WarehouseProduct)
class WarehouseProductAdmin(admin.ModelAdmin):
    change_list_template = "admin/warehouse/warehouseproduct/changelist.html"

    list_display = ('warehouse', 'product', 'quantity', 'threshold', 'batch_number', 'supplier')
    search_fields = ('warehouse__name', 'product__sku', 'product__name', 'supplier__name')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('upload-csv/', self.admin_site.admin_view(self.upload_csv), name='warehouse_warehouseproduct_upload_csv'),
            path('download-csv-template/', self.admin_site.admin_view(self.download_csv_template), name='warehouse_warehouseproduct_download_csv_template'),
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
                warehouse_name = row.get('warehouse')
                sku = row.get('sku')
                quantity = row.get('quantity')
                threshold = row.get('threshold')
                batch_number = row.get('batch_number')
                supplier_name = row.get('supplier')

                if not sku or not warehouse_name:
                    errors.append(f"Row {line_num}: SKU and Warehouse are required.")
                    continue

                warehouse = Warehouse.objects.filter(name=warehouse_name).first()
                if not warehouse:
                    errors.append(f"Row {line_num}: Warehouse '{warehouse_name}' not found.")
                    continue

                product = Product.objects.filter(sku=sku).first()
                if not product:
                    errors.append(f"Row {line_num}: Product with SKU '{sku}' not found. Please upload it first via Product CSV.")
                    continue

                supplier = Supplier.objects.filter(name=supplier_name).first() if supplier_name else None

                try:
                    quantity = int(quantity) if quantity else 0
                    threshold = int(threshold) if threshold else 0
                except ValueError:
                    errors.append(f"Row {line_num}: Invalid quantity or threshold format.")
                    continue

                if not batch_number:
                    today_str = datetime.now().strftime('%Y%m%d')
                    batch_number = f"{sku}-{today_str}-000"

                obj, created = WarehouseProduct.objects.update_or_create(
                    warehouse=warehouse,
                    product=product,
                    batch_number=batch_number,
                    defaults={
                        'quantity': quantity,
                        'threshold': threshold,
                        'supplier': supplier,
                    }
                )

                if created:
                    created_count += 1
                else:
                    updated_count += 1

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
            headers={'Content-Disposition': 'attachment; filename="warehouseproduct_upload_template.csv"'},
        )
        writer = csv.writer(response)
        writer.writerow(['warehouse', 'sku', 'quantity', 'threshold', 'batch_number', 'supplier'])
        writer.writerow(['Example Warehouse', 'SKU123', '100', '10', '', 'Supplier Name'])
        return response


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'location')

@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'supplier', 'status', 'draft_date', 'waiting_invoice_date', 'payment_made_date',
        'partially_delivered_date', 'delivered_date', 'cancelled_date',
        'inventory_updated'
    )
    list_filter = ('status', 'draft_date', 'supplier')
    search_fields = ('id', 'supplier__name')
    ordering = ('-draft_date',)


admin.site.register(PurchaseOrderItem)
