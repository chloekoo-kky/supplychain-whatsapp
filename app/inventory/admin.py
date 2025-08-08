# app/inventory/admin.py

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render
from django.urls import path
from django import forms
from django.http import HttpResponse
import csv
import chardet
from decimal import Decimal, InvalidOperation
from datetime import datetime
import logging

# Import your models
from .models import (
    Product, Supplier, StockTransaction, InventoryBatchItem,
    StockTakeSession, StockTakeItem,
     ErpStockCheckSession, ErpStockCheckItem, WarehouseProductDiscrepancy, # New Stock Take Models
     PackagingMaterial, WarehousePackagingMaterial, PackagingStockTransaction
)
from warehouse.models import Warehouse, WarehouseProduct # Import WarehouseProduct
from .models import StockDiscrepancy

logger = logging.getLogger(__name__)
User = get_user_model()

# Define a simple form for CSV upload to be used in the modal
class CsvImportForm(forms.Form):
    csv_upload = forms.FileField(label="Select CSV file")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    change_list_template = "admin/inventory/product/changelist.html"
    list_display = ('sku', 'name', 'price', 'created_date')
    search_fields = ('sku', 'name')
    list_filter = ('name',)
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
            form = CsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = request.FILES["csv_upload"]

                try:
                    raw_data = csv_file.read()
                    result = chardet.detect(raw_data)
                    encoding = result['encoding'] if result['encoding'] else 'utf-8'
                    decoded_file = raw_data.decode(encoding).splitlines()
                    reader = csv.DictReader(decoded_file)
                except Exception as e:
                    self.message_user(request, f"Error reading or decoding CSV file: {e}", level=messages.ERROR)
                    return redirect("..")

                created_count = 0
                updated_count = 0
                errors = []

                for line_num, row in enumerate(reader, start=2):
                    sku = row.get('sku','').strip()
                    name = row.get('name','').strip()
                    price_str = row.get('price','').strip()
                    supplier_code_str = row.get('supplier_code','').strip()

                    if not sku:
                        errors.append(f"Row {line_num}: SKU is required.")
                        continue

                    supplier = None
                    if supplier_code_str:
                        try:
                            supplier = Supplier.objects.get(code__iexact=supplier_code_str)
                        except Supplier.DoesNotExist:
                            errors.append(f"Row {line_num}: Supplier with code '{supplier_code_str}' not found. Product will be processed without this supplier or with its existing supplier if updating.")

                    try:
                        price = Decimal(price_str) if price_str else Decimal('0.00')
                    except InvalidOperation:
                        errors.append(f"Row {line_num}: Invalid price format for '{price_str}'.")
                        continue

                    product_defaults = {
                        'name': name or f"Unnamed Product ({sku})",
                        'price': price,
                    }
                    if supplier:
                        product_defaults['supplier'] = supplier
                    elif supplier_code_str:
                         product_defaults['supplier'] = None


                    product, created = Product.objects.update_or_create(
                        sku=sku,
                        defaults=product_defaults
                    )

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                summary_message = f"Product CSV Upload: {created_count} created, {updated_count} updated."
                if errors:
                    for error in errors:
                        self.message_user(request, error, level=messages.WARNING)
                    summary_message += f" Encountered {len(errors)} issue(s)."
                    self.message_user(request, summary_message, level=messages.WARNING)
                else:
                    self.message_user(request, summary_message, level=messages.SUCCESS)
                return redirect("..")
            else:
                for field, field_errors in form.errors.items():
                    for error in field_errors:
                        self.message_user(request, f"Form error in {field}: {error}", level=messages.ERROR)

        self.message_user(request, "Please select a CSV file to upload using the form.", level=messages.INFO)
        return redirect("..")


    def download_csv_template(self, request):
        response = HttpResponse(
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="product_upload_template.csv"'},
        )
        writer = csv.writer(response)
        writer.writerow(['sku', 'name', 'price'])
        return response

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'email', 'whatsapp_number')
    search_fields = ('name', 'code')

@admin.register(StockTransaction)
class StockTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'product_display', 'warehouse_display', 'transaction_type',
        'quantity', 'reference_note', 'related_po_display', 'related_order_display', 'transaction_date_formatted'
    )
    list_filter = (
        'transaction_type', 'warehouse',
        ('transaction_date', admin.DateFieldListFilter),
        'product',
    )
    search_fields = (
        'product__name', 'product__sku',
        'warehouse__name', 'reference_note',
        'related_po__id', 'related_order__id'
    )
    ordering = ('-transaction_date',)
    readonly_fields = ('transaction_date',)

    fields = (
        'warehouse', 'warehouse_product', 'product',
        'transaction_type', 'quantity', 'reference_note',
        'related_po', 'related_order', 'transaction_date'
    )

    def product_display(self, obj):
        return obj.product.name if obj.product else "-"
    product_display.short_description = 'Product'
    product_display.admin_order_field = 'product__name'

    def warehouse_display(self, obj):
        return obj.warehouse.name if obj.warehouse else "-"
    warehouse_display.short_description = 'Warehouse'
    warehouse_display.admin_order_field = 'warehouse__name'

    def related_po_display(self, obj):
        return f"PO#{obj.related_po.id}" if obj.related_po else "-"
    related_po_display.short_description = 'Related PO'

    def related_order_display(self, obj):
        return f"Order#{obj.related_order.id}" if obj.related_order else "-"
    related_order_display.short_description = 'Related Order'

    def transaction_date_formatted(self, obj):
        return obj.transaction_date.strftime("%Y-%m-%d %H:%M:%S") if obj.transaction_date else "-"
    transaction_date_formatted.short_description = 'Transacted At'
    transaction_date_formatted.admin_order_field = 'transaction_date'


@admin.register(InventoryBatchItem)
class InventoryBatchItemAdmin(admin.ModelAdmin):
    change_list_template = "admin/inventory/inventorybatchitem/changelist.html"
    list_display = (
        'id',
        'get_warehouse_product_sku',
        'product_name_display',
        'warehouse_name_display',
        'batch_number',
        'location_label', # Display location_label
        'expiry_date',
        'quantity',
        'pick_priority',
        'cost_price',
        'date_received',
        'last_modified'
    )
    list_filter = (
        'warehouse_product__warehouse',
        'pick_priority',
        'expiry_date',
        'date_received',
        'warehouse_product__product',
        'warehouse_product__supplier',
        'location_label', # Filter by location_label
    )
    search_fields = (
        'batch_number',
        'location_label', # Search by location_label
        'warehouse_product__product__sku',
        'warehouse_product__product__name',
        'warehouse_product__warehouse__name',
        'warehouse_product__supplier__code',
        'warehouse_product__supplier__name',
    )
    ordering = ('warehouse_product__product__name', 'pick_priority', 'expiry_date')
    readonly_fields = ('created_at', 'last_modified')
    autocomplete_fields = ['warehouse_product']
    list_editable = ('pick_priority',) # Allow direct editing of priority

    fieldsets = (
        (None, {
            'fields': ('warehouse_product', 'batch_number', 'location_label', 'quantity', 'pick_priority') # Added pick_priority
        }),
        ('Dates', {
            'fields': ('expiry_date', 'date_received')
        }),
        ('Financials', {
            'fields': ('cost_price',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'last_modified'),
            'classes': ('collapse',)
        }),
    )

    def pick_priority_display(self, obj):
        return obj.get_pick_priority_display() # Uses Django's get_FOO_display for choices
    pick_priority_display.short_description = 'Pick Priority'
    pick_priority_display.admin_order_field = 'pick_priority'

    def get_warehouse_product_sku(self, obj):
        if obj.warehouse_product and obj.warehouse_product.product:
            return obj.warehouse_product.product.sku
        return 'N/A'
    get_warehouse_product_sku.short_description = 'Product SKU'
    get_warehouse_product_sku.admin_order_field = 'warehouse_product__product__sku'

    def product_name_display(self, obj):
        return obj.warehouse_product.product.name if obj.warehouse_product and obj.warehouse_product.product else 'N/A'
    product_name_display.short_description = 'Product Name'
    product_name_display.admin_order_field = 'warehouse_product__product__name'

    def warehouse_name_display(self, obj):
        return obj.warehouse_product.warehouse.name if obj.warehouse_product and obj.warehouse_product.warehouse else 'N/A'
    warehouse_name_display.short_description = 'Warehouse'
    warehouse_name_display.admin_order_field = 'warehouse_product__warehouse__name'

    def get_supplier_code(self, obj):
        if obj.warehouse_product and \
           obj.warehouse_product.product and \
           obj.warehouse_product.product.supplier:
            return obj.warehouse_product.product.supplier.code
        return 'N/A'
    get_supplier_code.short_description = 'Supplier Code'
    get_supplier_code.admin_order_field = 'warehouse_product__supplier__code'

    # --- CSV Upload/Download Methods for InventoryBatchItem ---
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('upload-batch-csv/', self.admin_site.admin_view(self.upload_batch_csv), name='inventory_inventorybatchitem_upload_csv'),
            path('download-batch-csv-template/', self.admin_site.admin_view(self.download_batch_csv_template), name='inventory_inventorybatchitem_download_template'),
        ]
        return custom_urls + urls

    def download_batch_csv_template(self, request):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="inventory_batch_item_template.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'product_sku', 'warehouse_name', 'batch_number', 'location_label', # Added location_label
            'expiry_date (YYYY-MM-DD)', 'quantity', 'cost_price', 'date_received (YYYY-MM-DD)'
        ])
        writer.writerow([
            'SKU001', 'Main Warehouse', 'BATCH001A', 'A01-R1-S1', # Example location_label
            '2026-12-31', '100', '10.50', '2025-05-14'
        ])
        writer.writerow([
            'SKU001', 'Main Warehouse', 'BATCH001A', 'A01-R1-S2', # Same batch, different location
            '2026-12-31', '50', '10.50', '2025-05-14'
        ])
        writer.writerow([
            'SKU002', 'Secondary Warehouse', 'B002-XYZ', '', # Empty location_label
            '', '75', '22.75', ''
        ])
        return response

    def upload_batch_csv(self, request):
        if request.method == "POST":
            form = CsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = request.FILES["csv_upload"]
                try:
                    raw_data = csv_file.read()
                    result = chardet.detect(raw_data)
                    encoding = result['encoding'] if result['encoding'] else 'utf-8'
                    decoded_file = raw_data.decode(encoding).splitlines()
                    reader = csv.DictReader(decoded_file)
                except Exception as e:
                    self.message_user(request, f"Error reading or decoding CSV file: {e}", level=messages.ERROR)
                    return redirect("..")

                created_count = 0
                updated_count = 0
                skipped_count = 0
                errors = []

                for line_num, row in enumerate(reader, start=2):
                    product_sku = row.get('product_sku','').strip()
                    warehouse_name = row.get('warehouse_name','').strip()
                    batch_number = row.get('batch_number','').strip()
                    location_label_csv = row.get('location_label','').strip() # Get location_label
                    expiry_date_str = row.get('expiry_date (YYYY-MM-DD)','').strip()
                    quantity_str = row.get('quantity','').strip()
                    cost_price_str = row.get('cost_price','').strip()
                    date_received_str = row.get('date_received (YYYY-MM-DD)','').strip()

                    if not product_sku or not warehouse_name:
                        errors.append(f"Row {line_num}: Product SKU and Warehouse Name are required.")
                        skipped_count +=1
                        continue

                    if not batch_number: # Batch number is critical for the key
                        errors.append(f"Row {line_num}: Batch Number is required.")
                        skipped_count +=1
                        continue

                    # Handle location_label: if empty string, treat as None for DB uniqueness
                    location_label_for_db = location_label_csv if location_label_csv else None

                    try:
                        # Using iexact for case-insensitive matching
                        product = Product.objects.get(sku__iexact=product_sku)
                        warehouse = Warehouse.objects.get(name__iexact=warehouse_name)
                        warehouse_product = WarehouseProduct.objects.get(product=product, warehouse=warehouse)
                    except Product.DoesNotExist:
                        errors.append(f"Row {line_num}: Product with SKU '{product_sku}' not found.")
                        skipped_count +=1
                        continue
                    except Warehouse.DoesNotExist:
                        errors.append(f"Row {line_num}: Warehouse '{warehouse_name}' not found.")
                        skipped_count +=1
                        continue
                    except WarehouseProduct.DoesNotExist:
                        errors.append(f"Row {line_num}: WarehouseProduct for SKU '{product_sku}' in Warehouse '{warehouse_name}' not found. Please ensure it exists.")
                        skipped_count +=1
                        continue

                    defaults = {}
                    try:
                        defaults['quantity'] = int(quantity_str) if quantity_str else 0
                    except ValueError:
                        errors.append(f"Row {line_num}: Invalid quantity '{quantity_str}'. Must be a number.")
                        skipped_count +=1
                        continue

                    if expiry_date_str:
                        try:
                            defaults['expiry_date'] = datetime.strptime(expiry_date_str, '%Y-%m-%d').date()
                        except ValueError:
                            errors.append(f"Row {line_num}: Invalid expiry_date format '{expiry_date_str}'. Use YYYY-MM-DD.")
                            skipped_count +=1
                            continue
                    else:
                        defaults['expiry_date'] = None

                    if cost_price_str:
                        try:
                            defaults['cost_price'] = Decimal(cost_price_str)
                        except InvalidOperation:
                            errors.append(f"Row {line_num}: Invalid cost_price '{cost_price_str}'.")
                            skipped_count +=1
                            continue
                    else:
                        defaults['cost_price'] = None

                    if date_received_str:
                        try:
                            defaults['date_received'] = datetime.strptime(date_received_str, '%Y-%m-%d').date()
                        except ValueError:
                            errors.append(f"Row {line_num}: Invalid date_received format '{date_received_str}'. Use YYYY-MM-DD.")
                            skipped_count +=1
                            continue
                    # If date_received is not in CSV, it will use model default on creation or remain unchanged on update.

                    try:
                        # Key for update_or_create now includes location_label_for_db
                        batch_item, created = InventoryBatchItem.objects.update_or_create(
                            warehouse_product=warehouse_product,
                            batch_number=batch_number,
                            location_label=location_label_for_db, # Use the processed label
                            defaults=defaults
                        )
                        if created:
                            created_count += 1
                        else:
                            updated_count += 1
                    except Exception as e:
                        errors.append(f"Row {line_num}: Error saving batch for SKU '{product_sku}', Batch '{batch_number}', Location '{location_label_csv}': {e}")
                        skipped_count +=1
                        continue

                summary_message = f"Inventory Batch CSV Upload: {created_count} created, {updated_count} updated, {skipped_count} skipped."
                if errors:
                    for error in errors:
                        self.message_user(request, error, level=messages.WARNING)
                    self.message_user(request, summary_message, level=messages.WARNING)
                else:
                    self.message_user(request, summary_message, level=messages.SUCCESS)
                return redirect("..")
            else:
                 for field, field_errors in form.errors.items():
                    for error in field_errors:
                        self.message_user(request, f"Form error in {field}: {error}", level=messages.ERROR)

        self.message_user(request, "Please select a CSV file to upload using the form.", level=messages.INFO)
        return redirect("..")

# --- New Stock Take Admin Registrations ---

class StockTakeItemInline(admin.TabularInline):
    model = StockTakeItem
    fields = (
        'warehouse_product',
        'location_label_counted',
        'batch_number_counted',
        'expiry_date_counted',
        'counted_quantity',
        'notes',
        'counted_at'
    )
    readonly_fields = ('counted_at',)
    extra = 0 # Don't show extra empty forms by default in session view
    autocomplete_fields = ['warehouse_product']

    def get_formset(self, request, obj=None, **kwargs):
        # Pass the warehouse of the parent StockTakeSession to the formset forms
        # So that 'warehouse_product' dropdown can be filtered.
        Formset = super().get_formset(request, obj, **kwargs)
        class StockTakeItemFormSetWithWarehouse(Formset):
            def __init__(self, *args, **inner_kwargs):
                # If creating a new StockTakeSession, obj will be None.
                # If editing an existing one, obj is the StockTakeSession instance.
                self.warehouse_instance = obj.warehouse if obj else None
                super().__init__(*args, **inner_kwargs)

            def _construct_form(self, i, **form_kwargs):
                form = super()._construct_form(i, **form_kwargs)
                # Pass warehouse to each form instance
                form.warehouse = self.warehouse_instance
                # Re-filter the queryset for the warehouse_product field
                if self.warehouse_instance:
                    form.fields['warehouse_product'].queryset = WarehouseProduct.objects.filter(
                        warehouse=self.warehouse_instance
                    ).select_related('product').order_by('product__name')
                else: # Should not happen if session always has a warehouse
                    form.fields['warehouse_product'].queryset = WarehouseProduct.objects.none()
                form.fields['warehouse_product'].label_from_instance = lambda o: f"{o.product.name} (SKU: {o.product.sku})"
                return form
        return StockTakeItemFormSetWithWarehouse


@admin.register(StockTakeSession)
class StockTakeSessionAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'warehouse',
        'status',
        'initiated_by',
        'initiated_at',
        'completed_by_operator_at',
        'evaluated_by',
        'evaluated_at'
    )
    list_filter = ('status', 'warehouse', 'initiated_at', 'evaluated_at')
    search_fields = ('name', 'warehouse__name', 'initiated_by__email', 'initiated_by__name')
    ordering = ('-initiated_at',)
    readonly_fields = ('initiated_at', 'completed_by_operator_at', 'evaluated_at')
    inlines = [StockTakeItemInline]
    autocomplete_fields = ['warehouse', 'initiated_by', 'evaluated_by']

    fieldsets = (
        (None, {'fields': ('name', 'warehouse', 'status', 'notes')}),
        ('User and Timestamps', {
            'fields': ('initiated_by', 'initiated_at', 'completed_by_operator_at', 'evaluated_by', 'evaluated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('warehouse', 'initiated_by', 'evaluated_by')


@admin.register(StockTakeItem)
class StockTakeItemAdmin(admin.ModelAdmin):
    list_display = (
        'session_link',
        'warehouse_product_display',
        'location_label_counted',
        'batch_number_counted',
        'expiry_date_counted',
        'counted_quantity',
        'counted_at'
    )
    list_filter = ('session__warehouse', 'session__status', 'expiry_date_counted', 'counted_at')
    search_fields = (
        'session__name',
        'warehouse_product__product__sku',
        'warehouse_product__product__name',
        'location_label_counted',
        'batch_number_counted'
    )
    ordering = ('-counted_at',)
    autocomplete_fields = ['session', 'warehouse_product']
    readonly_fields = ('counted_at',)

    def session_link(self, obj):
        from django.urls import reverse
        from django.utils.html import format_html
        if obj.session:
            link = reverse("admin:inventory_stocktakesession_change", args=[obj.session.id])
            return format_html('<a href="{}">{}</a>', link, obj.session.name)
        return "-"
    session_link.short_description = 'Stock Take Session'
    session_link.admin_order_field = 'session__name'

    def warehouse_product_display(self, obj):
        if obj.warehouse_product:
            return f"{obj.warehouse_product.product.sku} - {obj.warehouse_product.product.name} @ {obj.warehouse_product.warehouse.name}"
        return "-"
    warehouse_product_display.short_description = 'Warehouse Product (System)'
    warehouse_product_display.admin_order_field = 'warehouse_product__product__name'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related(
            'session__warehouse',
            'warehouse_product__product',
            'warehouse_product__warehouse'
        )

@admin.register(StockDiscrepancy)
class StockDiscrepancyAdmin(admin.ModelAdmin):
    list_display = (
        'session',
        'warehouse_product_display',
        'discrepancy_type',
        'system_quantity',
        'counted_quantity',
        'discrepancy_quantity',
        'is_resolved',
        'evaluated_at'
    )
    list_editable = ('is_resolved',)
    list_filter = ('session__warehouse', 'discrepancy_type', 'is_resolved', 'evaluated_at', 'session')
    search_fields = (
        'session__name',
        'warehouse_product__product__sku',
        'warehouse_product__product__name',
        'system_batch_number',
        'counted_batch_number'
    )
    readonly_fields = (
        'session', 'warehouse_product', 'system_inventory_batch_item',
        'system_location_label', 'system_batch_number', 'system_expiry_date', 'system_quantity',
        'stock_take_item_reference', 'counted_location_label', 'counted_batch_number',
        'counted_expiry_date', 'counted_quantity', 'discrepancy_quantity',
        'discrepancy_type', 'evaluated_at'
    ) # Most fields are derived or set by the evaluation process

    fieldsets = (
        ("Discrepancy Info", {
            'fields': ('session', 'warehouse_product', 'discrepancy_type', 'discrepancy_quantity')
        }),
        ("System Details", {
            'fields': ('system_inventory_batch_item', 'system_location_label', 'system_batch_number', 'system_expiry_date', 'system_quantity'),
            'classes': ('collapse',)
        }),
        ("Counted Details", {
            'fields': ('stock_take_item_reference', 'counted_location_label', 'counted_batch_number', 'counted_expiry_date', 'counted_quantity'),
            'classes': ('collapse',)
        }),
        ("Resolution", {
            'fields': ('is_resolved', 'resolution_notes', 'resolved_at', 'resolved_by', 'notes')
        }),
        ("Timestamps", {
            'fields': ('evaluated_at',),
            'classes': ('collapse',)
        })
    )
    autocomplete_fields = ['resolved_by']


    def warehouse_product_display(self, obj):
        if obj.warehouse_product:
            return f"{obj.warehouse_product.product.sku} @ {obj.warehouse_product.warehouse.name}"
        return "-"
    warehouse_product_display.short_description = 'Warehouse Product'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'session__warehouse',
            'warehouse_product__product',
            'warehouse_product__warehouse'
        )

@admin.register(ErpStockCheckSession)
class ErpStockCheckSessionAdmin(admin.ModelAdmin):
    list_display = ('name', 'warehouse', 'status', 'uploaded_by', 'uploaded_at', 'evaluated_by', 'evaluated_at', 'source_file_name')
    list_filter = ('status', 'warehouse', 'uploaded_at', 'evaluated_at')
    search_fields = ('name', 'warehouse__name', 'uploaded_by__email', 'source_file_name')
    readonly_fields = ('uploaded_at', 'processed_at', 'evaluated_at')
    autocomplete_fields = ['warehouse', 'uploaded_by', 'evaluated_by']

@admin.register(ErpStockCheckItem)
class ErpStockCheckItemAdmin(admin.ModelAdmin):
    list_display = ('session', 'warehouse_product_display', 'erp_quantity', 'is_matched', 'processing_comments')
    list_filter = ('session__name', 'is_matched', 'warehouse_product__warehouse__name')
    search_fields = ('session__name', 'warehouse_product__product__sku', 'warehouse_product__product__name', 'erp_product_sku_raw')
    autocomplete_fields = ['session', 'warehouse_product']

    def warehouse_product_display(self, obj):
        return str(obj.warehouse_product)
    warehouse_product_display.short_description = "Warehouse Product (System)"

@admin.register(WarehouseProductDiscrepancy)
class WarehouseProductDiscrepancyAdmin(admin.ModelAdmin):
    list_display = ('session', 'warehouse_product_display', 'discrepancy_type', 'system_quantity', 'erp_quantity', 'discrepancy_quantity', 'is_resolved', 'created_at')
    list_filter = ('session__name', 'discrepancy_type', 'is_resolved', 'warehouse_product__warehouse__name')
    search_fields = ('session__name', 'warehouse_product__product__sku', 'warehouse_product__product__name')
    readonly_fields = ('created_at', 'discrepancy_quantity') # Some fields are auto-calculated or set by system
    autocomplete_fields = ['session', 'warehouse_product', 'erp_stock_check_item', 'resolved_by']

    def warehouse_product_display(self, obj):
        return str(obj.warehouse_product)
    warehouse_product_display.short_description = "Warehouse Product"



class WarehousePackagingMaterialInline(admin.TabularInline):
    """
    Manages warehouse-specific stock from the global PackagingMaterial admin page.
    """
    model = WarehousePackagingMaterial
    extra = 1
    autocomplete_fields = ['warehouse']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if request.user.warehouse:
            return qs.filter(warehouse=request.user.warehouse)
        return qs.none()


@admin.register(PackagingMaterial)
class PackagingMaterialAdmin(admin.ModelAdmin):
    """
    Admin for the GLOBAL PackagingMaterial definitions.
    """
    list_display = ('name', 'material_code', 'supplier')
    search_fields = ('name', 'material_code', 'supplier__name')
    list_filter = ('supplier',)
    autocomplete_fields = ['supplier']
    inlines = [WarehousePackagingMaterialInline]


@admin.register(WarehousePackagingMaterial)
class WarehousePackagingMaterialAdmin(admin.ModelAdmin):
    """
    Admin for viewing and managing WAREHOUSE-SPECIFIC stock levels.
    """
    list_display = ('packaging_material', 'warehouse', 'current_stock', 'reorder_level')
    search_fields = ('packaging_material__name', 'warehouse__name')
    list_filter = ('warehouse',)
    autocomplete_fields = ['packaging_material', 'warehouse']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if request.user.warehouse:
            return qs.filter(warehouse=request.user.warehouse)
        return qs.none()


@admin.register(PackagingStockTransaction)
class PackagingStockTransactionAdmin(admin.ModelAdmin):
    """
    Admin for viewing the historical log of received stock.
    """
    list_display = ('warehouse_packaging_material', 'transaction_type', 'quantity', 'recorded_by', 'transaction_date')
    autocomplete_fields = ['warehouse_packaging_material', 'recorded_by']
    list_filter = ('transaction_date', 'transaction_type', 'warehouse_packaging_material__warehouse')
    search_fields = (
        'warehouse_packaging_material__packaging_material__name',
        'warehouse_packaging_material__warehouse__name',
        'recorded_by__email'
    )

