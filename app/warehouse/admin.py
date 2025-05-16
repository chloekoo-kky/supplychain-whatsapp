# ===== warehouse/admin.py =====
import csv
# import chardet # Ensure chardet is installed in your environment
import openpyxl
from openpyxl.utils import get_column_letter

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponse
from django.urls import path, reverse
from django.shortcuts import redirect
from django.utils.html import format_html
from django.db import transaction # For atomic operations
from django.contrib.admin.views.main import ChangeList

# Import your models
from .models import Warehouse, WarehouseProduct, PurchaseOrder, PurchaseOrderItem
from inventory.models import Product, Supplier, StockTransaction # Make sure StockTransaction is imported


class WarehouseProductCsvImportForm(forms.Form):
    csv_upload = forms.FileField()

class ExcelImportForm(forms.Form):
    excel_upload = forms.FileField(label="Select Excel file (.xlsx)")


@admin.register(WarehouseProduct)
class WarehouseProductAdmin(admin.ModelAdmin):
    change_list_template = "admin/warehouse/warehouseproduct/changelist.html"
    list_display = (
        'get_warehouse_name',
        'code',
        'get_product_sku',
        'get_product_name',
        'quantity',
        'threshold',
        'get_supplier_code_display'
    )
    search_fields = (
        'warehouse__name',
        'code',
        'product__sku',
        'product__name',
        'supplier__name',
        'supplier__code'
    )
    list_filter = ('warehouse', 'supplier', 'product__name')
    autocomplete_fields = ['product', 'warehouse', 'supplier']
    ordering = ('warehouse__name', 'code', 'product__name')

    fieldsets = (
        (None, {
            'fields': ('warehouse', 'product', 'code', 'supplier')
        }),
        ('Stock Details', {
            'fields': ('quantity', 'threshold')
        }),
    )

    # Define consistent headers
    EXCEL_HEADERS = [
        'Warehouse Name', 'Product SKU', 'Warehouse Product Code',
        'Product Name', 'Quantity', 'Threshold',
        'Supplier Code' # Assuming this is WarehouseProduct.supplier.code
    ]

    def get_warehouse_name(self, obj):
        return obj.warehouse.name if obj.warehouse else "-"
    get_warehouse_name.short_description = 'Warehouse'
    get_warehouse_name.admin_order_field = 'warehouse__name'

    def get_product_sku(self, obj):
        return obj.product.sku if obj.product else "-"
    get_product_sku.short_description = 'SKU'
    get_product_sku.admin_order_field = 'product__sku'

    def get_product_name(self, obj):
        return obj.product.name if obj.product else "-"
    get_product_name.short_description = 'Product Name'
    get_product_name.admin_order_field = 'product__name'

    def get_supplier_code_display(self, obj):
        if obj.supplier:
            return obj.supplier.code
        return "-"
    get_supplier_code_display.short_description = 'Supplier Code'
    get_supplier_code_display.admin_order_field = 'supplier__code'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('upload-excel/', self.admin_site.admin_view(self.upload_excel), name='warehouse_warehouseproduct_upload_excel'),
            path('download-excel-template/', self.admin_site.admin_view(self.download_excel_template), name='warehouse_warehouseproduct_download_excel_template'),
            path('export-excel/', self.admin_site.admin_view(self.export_excel), name='warehouse_warehouseproduct_export_excel'),
        ]
        return custom_urls + urls

    def download_excel_template(self, request):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "WarehouseProduct Template"

        sheet.append(self.EXCEL_HEADERS)

        # Add example rows
        sheet.append(['Main Warehouse', 'SKU001', 'MW-SKU001', 'Sample Product One', 100, 10, 'SUP001'])
        sheet.append(['Secondary Warehouse', 'SKU002', 'SW-SKU002', 'Another Product', 50, 5, '']) # Example with no supplier

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename="warehouseproduct_template.xlsx"'
        workbook.save(response)
        return response

    def export_excel(self, request):
        sortable_by = self.get_sortable_by(request)
        cl = ChangeList(
            request, self.model, self.list_display, self.list_display_links,
            self.list_filter, self.date_hierarchy, self.search_fields,
            self.list_select_related, self.list_per_page, self.list_max_show_all,
            self.list_editable, self, sortable_by
        )
        queryset = cl.get_queryset(request).select_related('warehouse', 'product', 'supplier') # Ensure related objects are fetched

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Warehouse Products Export"
        sheet.append(self.EXCEL_HEADERS)

        for obj in queryset:
            sheet.append([
                obj.warehouse.name if obj.warehouse else '',
                obj.product.sku if obj.product else '',
                obj.code if obj.code else '',
                obj.product.name if obj.product else '',
                obj.quantity,
                obj.threshold,
                obj.supplier.code if obj.supplier else '',
            ])

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename="warehouse_products_export.xlsx"'
        workbook.save(response)
        return response
    export_excel.short_description = "Export Warehouse Products as Excel"

    def upload_excel(self, request):
        if request.method == "POST":
            form = ExcelImportForm(request.POST, request.FILES)
            if form.is_valid():
                excel_file = request.FILES["excel_upload"]
                try:
                    workbook = openpyxl.load_workbook(excel_file, data_only=True) # data_only=True to get values not formulas
                    sheet = workbook.active
                except Exception as e:
                    self.message_user(request, f"Error reading Excel file: {e}", level=messages.ERROR)
                    return redirect("..")

                created_count = 0
                updated_count = 0
                errors = []

                # Read headers from the first row
                header_row = [cell.value for cell in sheet[1]]
                if header_row != self.EXCEL_HEADERS:
                    errors.append(f"Invalid Excel headers. Expected: {', '.join(self.EXCEL_HEADERS)}. Got: {', '.join(header_row)}")
                    # Log all errors and redirect
                    for error in errors:
                        self.message_user(request, error, level=messages.ERROR)
                    return redirect("..")


                for row_idx, row in enumerate(sheet.iter_rows(min_row=2), start=2): # Skip header row
                    # Convert row to a dictionary using headers
                    try:
                        row_data = {self.EXCEL_HEADERS[i]: cell.value for i, cell in enumerate(row) if i < len(self.EXCEL_HEADERS)}
                    except IndexError:
                        errors.append(f"Row {row_idx}: Mismatch between header count and row cell count.")
                        continue


                    warehouse_name = str(row_data.get('Warehouse Name', '')).strip()
                    sku = str(row_data.get('Product SKU', '')).strip()
                    wp_code = str(row_data.get('Warehouse Product Code', '')).strip()
                    # Product Name is for reference, not directly used for lookup if SKU is primary
                    # product_name_excel = str(row_data.get('Product Name', '')).strip()
                    quantity_val = row_data.get('Quantity')
                    threshold_val = row_data.get('Threshold')
                    supplier_code_excel = str(row_data.get('Supplier Code', '')).strip()


                    if not sku or not warehouse_name:
                        errors.append(f"Row {row_idx}: 'Warehouse Name' and 'Product SKU' are required.")
                        continue

                    try:
                        warehouse = Warehouse.objects.get(name__iexact=warehouse_name)
                    except Warehouse.DoesNotExist:
                        errors.append(f"Row {row_idx}: Warehouse '{warehouse_name}' not found.")
                        continue
                    try:
                        product = Product.objects.get(sku__iexact=sku)
                    except Product.DoesNotExist:
                        errors.append(f"Row {row_idx}: Product with SKU '{sku}' not found.")
                        continue

                    wp_supplier = None
                    if supplier_code_excel:
                        try:
                            wp_supplier = Supplier.objects.get(code__iexact=supplier_code_excel)
                        except Supplier.DoesNotExist:
                            errors.append(f"Row {row_idx}: Supplier with code '{supplier_code_excel}' not found. Supplier field will be left blank for this item if it's new, or unchanged if updating and previously set.")

                    try:
                        quantity = int(quantity_val) if quantity_val is not None else 0
                        threshold = int(threshold_val) if threshold_val is not None else 0
                    except (ValueError, TypeError):
                        errors.append(f"Row {row_idx}: Invalid quantity or threshold for SKU '{sku}'. Must be numbers. Got Qty: '{quantity_val}', Threshold: '{threshold_val}'.")
                        continue

                    defaults = {
                        'quantity': quantity,
                        'threshold': threshold,
                        'supplier': wp_supplier,
                        # Only update code if provided, otherwise keep existing or let it be null
                        'code': wp_code if wp_code else None,
                    }

                    try:
                        obj, created = WarehouseProduct.objects.update_or_create(
                            warehouse=warehouse,
                            product=product,
                            defaults=defaults
                        )
                        if created:
                            created_count += 1
                        else:
                            updated_count += 1
                    except Exception as e:
                        errors.append(f"Row {row_idx}: Error saving WarehouseProduct for SKU '{sku}' (Warehouse: {warehouse_name}, Code: '{wp_code}'): {e}")

                summary_message = f"Excel Upload: {created_count} created, {updated_count} updated."
                if errors:
                    for error in errors:
                        self.message_user(request, error, level=messages.WARNING)
                    summary_message += f" Encountered {len(errors)} issue(s)."
                    self.message_user(request, summary_message, level=messages.WARNING)
                else:
                    self.message_user(request, summary_message, level=messages.SUCCESS)
                return redirect("..")
            else: # Form not valid
                for field, field_errors in form.errors.items():
                    for error in field_errors:
                        self.message_user(request, f"Form error in {field}: {error}", level=messages.ERROR)

        self.message_user(request, "Please select an Excel file (.xlsx) to upload.", level=messages.INFO)
        return redirect("..")

@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'location')
    search_fields = ('name', 'location')

class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    fields = ('item', 'quantity', 'price', 'received_quantity')
    autocomplete_fields = ['item'] # Use autocomplete for selecting WarehouseProduct
    extra = 1

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('item__product', 'item__warehouse')


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'get_supplier_code','get_supplier_name', 'status', 'total_amount_display', 'eta_formatted',
        'draft_date_formatted', 'payment_made_date_formatted',
        'delivered_date_formatted', 'last_updated_date_formatted'
    )
    list_filter = ('status', 'supplier', ('eta', admin.DateFieldListFilter), ('last_updated_date', admin.DateFieldListFilter))
    search_fields = ('id', 'supplier__name', 'supplier__code', 'items__item__product__sku', 'items__item__product__name')
    ordering = ('-last_updated_date',)
    inlines = [PurchaseOrderItemInline]
    date_hierarchy = 'last_updated_date'
    readonly_fields = ('created_at_display', 'last_updated_at_display', 'id') # Added id
    autocomplete_fields = ['supplier']


    fieldsets = (
        (None, {'fields': ('id', 'supplier', 'status', 'notes')}),
        ('Dates & Timestamps', {'fields': ('eta', 'draft_date', 'waiting_invoice_date', 'payment_made_date', 'partially_delivered_date', 'delivered_date', 'cancelled_date', 'created_at_display', 'last_updated_at_display'), 'classes':('collapse',)}),
    )

    def get_supplier_code(self, obj):
        return obj.supplier.code if obj.supplier else "-"
    get_supplier_code.short_description = 'Supplier Code'
    get_supplier_code.admin_order_field = 'supplier__code'

    def get_supplier_name(self, obj):
        return obj.supplier.name if obj.supplier else "-"
    get_supplier_name.short_description = 'Supplier Name'
    get_supplier_name.admin_order_field = 'supplier__name'

    def total_amount_display(self, obj):
        return obj.total_amount
    total_amount_display.short_description = 'Total Amount'

    def _format_date(self, date_obj, default_val="-", include_time=True):
        if not date_obj:
            return default_val
        if include_time:
            return date_obj.strftime("%Y-%m-%d %H:%M")
        return date_obj.strftime("%Y-%m-%d")

    def eta_formatted(self, obj): return self._format_date(obj.eta, include_time=False)
    eta_formatted.short_description = 'ETA'
    eta_formatted.admin_order_field = 'eta'

    def draft_date_formatted(self, obj): return self._format_date(obj.draft_date)
    draft_date_formatted.short_description = 'Drafted'
    draft_date_formatted.admin_order_field = 'draft_date'

    def payment_made_date_formatted(self, obj): return self._format_date(obj.payment_made_date)
    payment_made_date_formatted.short_description = 'Payment Made'
    payment_made_date_formatted.admin_order_field = 'payment_made_date'

    def delivered_date_formatted(self, obj): return self._format_date(obj.delivered_date)
    delivered_date_formatted.short_description = 'Delivered'
    delivered_date_formatted.admin_order_field = 'delivered_date'

    def last_updated_date_formatted(self, obj): return self._format_date(obj.last_updated_date)
    last_updated_date_formatted.short_description = 'Last Updated'
    last_updated_date_formatted.admin_order_field = 'last_updated_date'

    def created_at_display(self, obj):
        # Assuming PurchaseOrder model has 'created_at' (auto_now_add=True)
        # If not, you might need to add it or use 'draft_date' as a proxy.
        if hasattr(obj, 'created_at') and obj.created_at:
             return self._format_date(obj.created_at)
        return self._format_date(obj.draft_date) # Fallback
    created_at_display.short_description = 'Created At'

    def last_updated_at_display(self, obj):
        return self._format_date(obj.last_updated_date)
    last_updated_at_display.short_description = 'Last Updated At'


@admin.register(PurchaseOrderItem)
class PurchaseOrderItemAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'get_po_link',
        'get_po_status',
        'get_supplier_code',
        'get_product_sku',
        'get_product_name',
        'get_warehouse_name',
        'quantity',
        'price',
        'get_total_price',
        'received_quantity',
        'get_balance_quantity'
    )
    list_filter = (
        'purchase_order__status',
        'purchase_order__supplier',
        'item__product',
        'item__warehouse',
        ('item__product__supplier', admin.RelatedOnlyFieldListFilter), # Filter by product's supplier
    )
    search_fields = (
        'purchase_order__id',
        'item__product__sku',
        'item__product__name',
        'item__warehouse__name',
        'purchase_order__supplier__code',
        'purchase_order__supplier__name',
    )
    ordering = ('-purchase_order__last_updated_date', 'item__product__name')
    autocomplete_fields = ['purchase_order', 'item']
    readonly_fields = ('get_total_price_display', 'get_balance_quantity_display') # For detail view

    fieldsets = (
        (None, {'fields': ('purchase_order', 'item')}),
        ('Quantities & Pricing', {'fields': ('quantity', 'price', 'get_total_price_display', 'received_quantity', 'get_balance_quantity_display')}),
    )

    def get_po_link(self, obj):
        if obj.purchase_order:
            link = reverse("admin:warehouse_purchaseorder_change", args=[obj.purchase_order.id])
            return format_html('<a href="{}">PO #{}</a>', link, obj.purchase_order.id)
        return "-"
    get_po_link.short_description = 'Purchase Order'
    get_po_link.admin_order_field = 'purchase_order__id'

    def get_po_status(self, obj):
        return obj.purchase_order.get_status_display() if obj.purchase_order else "-"
    get_po_status.short_description = 'PO Status'
    get_po_status.admin_order_field = 'purchase_order__status'

    def get_supplier_code(self, obj):
        if obj.purchase_order and obj.purchase_order.supplier:
            return obj.purchase_order.supplier.code
        # Fallback logic (less common for POItem, as PO should have a supplier)
        elif obj.item and obj.item.supplier: # Supplier on WarehouseProduct
            return obj.item.supplier.code
        return "-"
    get_supplier_code.short_description = 'Supplier Code'
    get_supplier_code.admin_order_field = 'purchase_order__supplier__code'

    def get_product_sku(self, obj):
        return obj.item.product.sku if obj.item and obj.item.product else "-"
    get_product_sku.short_description = 'SKU'
    get_product_sku.admin_order_field = 'item__product__sku'

    def get_product_name(self, obj):
        return obj.item.product.name if obj.item and obj.item.product else "-"
    get_product_name.short_description = 'Product Name'
    get_product_name.admin_order_field = 'item__product__name'

    def get_warehouse_name(self, obj):
        return obj.item.warehouse.name if obj.item and obj.item.warehouse else "-"
    get_warehouse_name.short_description = 'Warehouse'
    get_warehouse_name.admin_order_field = 'item__warehouse__name'

    def get_total_price(self, obj): # For list_display
        return obj.total_price
    get_total_price.short_description = 'Total Price'

    def get_total_price_display(self, obj): # For readonly_fields in detail view
        return obj.total_price
    get_total_price_display.short_description = 'Total Price'


    def get_balance_quantity(self, obj): # For list_display
        return obj.balance_quantity
    get_balance_quantity.short_description = 'Balance Qty'

    def get_balance_quantity_display(self, obj): # For readonly_fields in detail view
        return obj.balance_quantity
    get_balance_quantity_display.short_description = 'Balance Qty'


    @transaction.atomic
    def delete_model(self, request, obj):
        """
        Override to adjust WarehouseProduct quantity and create StockTransaction
        when a PurchaseOrderItem is deleted.
        This handles deletion of a single item from its change form.
        """
        warehouse_product = obj.item
        quantity_to_reverse = obj.received_quantity # Use the actual received quantity

        if warehouse_product and quantity_to_reverse > 0:
            # Deduct the received quantity from the warehouse product
            warehouse_product.quantity -= quantity_to_reverse
            warehouse_product.save(update_fields=['quantity'])

            # Create a stock transaction to record this reversal
            StockTransaction.objects.create(
                warehouse=warehouse_product.warehouse,
                warehouse_product=warehouse_product,
                product=warehouse_product.product,
                transaction_type='ADJUST', # Or a more specific type like 'PO_ITEM_DEL_ADJ'
                quantity=-quantity_to_reverse, # Negative to indicate deduction
                reference_note=f"Deletion of received PO Item ID {obj.id} for PO #{obj.purchase_order.id}",
                related_po=obj.purchase_order
            )
            messages.success(request, f"Stock for {warehouse_product.product.name} in {warehouse_product.warehouse.name} reduced by {quantity_to_reverse} due to PO Item deletion.")

        # Also, update the PO's status if necessary, e.g., if it was 'DELIVERED'
        # and now it's not fully received anymore.
        po = obj.purchase_order
        super().delete_model(request, obj) # Delete the PurchaseOrderItem

        # After deletion, re-check and update PO status
        if po:
            is_now_fully_received = po.is_fully_received()
            if not is_now_fully_received and po.status == 'DELIVERED':
                po.status = 'PARTIALLY_DELIVERED' # Or back to PAYMENT_MADE if nothing else received
                # Check if any items are received at all for this PO
                if not po.items.filter(received_quantity__gt=0).exists():
                    # If no items are received anymore, maybe revert to an earlier status
                    # This depends on your exact workflow. For instance, if 'PAYMENT_MADE' is the status before any receiving.
                    po.status = 'PAYMENT_MADE' # Example, adjust as per your PO lifecycle
                po.save() # This will also update status dates via model's save method
            elif is_now_fully_received and po.status != 'DELIVERED':
                 po.status = 'DELIVERED' # Should not happen if we just deleted a received item
                 po.save()


    @transaction.atomic
    def delete_queryset(self, request, queryset):
        """
        Override to adjust WarehouseProduct quantities and create StockTransactions
        for bulk deletion of PurchaseOrderItems from the changelist.
        """
        pos_to_recheck_status = set() # Keep track of POs whose status might need update

        for obj in queryset:
            warehouse_product = obj.item
            quantity_to_reverse = obj.received_quantity

            if warehouse_product and quantity_to_reverse > 0:
                warehouse_product.quantity -= quantity_to_reverse
                warehouse_product.save(update_fields=['quantity'])

                StockTransaction.objects.create(
                    warehouse=warehouse_product.warehouse,
                    warehouse_product=warehouse_product,
                    product=warehouse_product.product,
                    transaction_type='ADJUST',
                    quantity=-quantity_to_reverse,
                    reference_note=f"Bulk deletion of received PO Item ID {obj.id} for PO #{obj.purchase_order.id}",
                    related_po=obj.purchase_order
                )
            pos_to_recheck_status.add(obj.purchase_order)

        num_deleted = queryset.count()
        queryset.delete() # Perform the actual deletion

        # After deletion, re-check and update status for affected POs
        for po in pos_to_recheck_status:
            if po: # Ensure PO object still exists (it should)
                is_now_fully_received = po.is_fully_received()
                if not is_now_fully_received and po.status == 'DELIVERED':
                    po.status = 'PARTIALLY_DELIVERED'
                    if not po.items.filter(received_quantity__gt=0).exists():
                         po.status = 'PAYMENT_MADE' # Example
                    po.save()
                # No need to check if is_now_fully_received and status != DELIVERED, as we are deleting items.

        self.message_user(request, f"Successfully deleted {num_deleted} purchase order item(s) and adjusted stock accordingly.", messages.SUCCESS)

