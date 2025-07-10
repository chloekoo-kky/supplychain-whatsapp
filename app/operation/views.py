# app/operation/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST # For delete view
from django.core.serializers.json import DjangoJSONEncoder

from django.contrib import messages
from django.utils import timezone
from django.urls import reverse

from django.db import transaction, IntegrityError, models
from django.db.models import Q, Count, Prefetch, F, Value, Case, When, Sum, ExpressionWrapper, DurationField
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date
from django.http import JsonResponse, Http404, HttpResponse, HttpResponseForbidden
from django.template.loader import render_to_string
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

import logging
import traceback
import datetime
import calendar

from decimal import Decimal
from openpyxl import Workbook

import openpyxl
import xlrd
import json

from .forms import (
    ExcelImportForm, ParcelItemFormSet, InitialParcelItemFormSet,
    RemoveOrderItemForm, RemoveOrderItemFormSet,
    ParcelCustomsDetailForm, ParcelItemCustomsDetailFormSet, CustomsDeclarationForm,
    PackagingTypeForm, PackagingMaterialForm, AirwayBillForm, CourierInvoiceForm,
    DisputeForm, DisputeUpdateForm
)
from .models import (Order,
                     OrderItem,
                     Parcel,
                     ParcelItem,
                     CustomsDeclaration,
                     CourierCompany,
                     PackagingType,
                     PackagingTypeMaterialComponent,
                     ParcelTrackingLog,
                     CourierInvoice,
                     CourierInvoiceItem)
from inventory.models import Product, InventoryBatchItem, StockTransaction, PackagingMaterial
from warehouse.models import Warehouse, WarehouseProduct
from inventory.services import get_suggested_batch_for_order_item
from customers.utils import get_or_create_customer_from_import
from customers.models import Customer
from .services import update_parcel_tracking_from_api, parse_invoice_file



logger = logging.getLogger(__name__)
DEFAULT_CUSTOMER_ORDERS_TAB = "customer_orders"
DEFAULT_PARCELS_TAB = "parcels_details"


@login_required
def order_list_view(request):
    logger.debug(f"[OrderListView] Request GET params: {request.GET}")
    user = request.user

    active_tab = request.GET.get('tab', DEFAULT_CUSTOMER_ORDERS_TAB)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    # Use fetch_dynamic_content_only for customer orders, and fetch_parcel_list_only for parcels
    fetch_dynamic_content_only_co = request.GET.get('fetch_dynamic_content_only') == 'true'

    all_warehouses_qs = Warehouse.objects.all().order_by('name')
    status_choices = Order.STATUS_CHOICES
    import_form = ExcelImportForm()

    context = {
        'status_choices': status_choices,
        'import_form': import_form,
        'active_tab': active_tab,
        'DEFAULT_CUSTOMER_ORDERS_TAB': DEFAULT_CUSTOMER_ORDERS_TAB,
        'DEFAULT_PARCELS_TAB': DEFAULT_PARCELS_TAB,
        'request': request,
    }

    if active_tab == DEFAULT_CUSTOMER_ORDERS_TAB:
        selected_warehouse_id = request.GET.get('warehouse')
        selected_status = request.GET.get('status')
        query = request.GET.get('q', '').strip()
        page_number = request.GET.get('page', 1)
        logger.debug(f"[CustomerOrdersTab] Filters: warehouse='{selected_warehouse_id}', status='{selected_status}', q='{query}', page='{page_number}'")

        orders_qs = Order.objects.select_related(
            'warehouse', 'imported_by', 'customer'
        ).prefetch_related(
            Prefetch('items', queryset=OrderItem.objects.select_related('product', 'suggested_batch_item', 'warehouse_product').order_by('product__name')),
            # FIX: Added select_related('courier_company') to efficiently fetch the courier name.
            Prefetch('parcels', queryset=Parcel.objects.select_related('courier_company').order_by('-created_at'))
        ).all()

        warehouses_for_co_filters = all_warehouses_qs
        if not user.is_superuser:
            if user.warehouse:
                warehouses_for_co_filters = Warehouse.objects.filter(pk=user.warehouse.pk)
                orders_qs = orders_qs.filter(warehouse=user.warehouse)
                selected_warehouse_id = str(user.warehouse.pk)
            else:
                warehouses_for_co_filters = Warehouse.objects.none()
                orders_qs = orders_qs.none()
        context['warehouses'] = warehouses_for_co_filters # Keep this for customer orders tab

        if selected_warehouse_id and user.is_superuser:
            orders_qs = orders_qs.filter(warehouse_id=selected_warehouse_id)

        if selected_status:
            orders_qs = orders_qs.filter(status=selected_status)
        if query:
            orders_qs = orders_qs.filter(
                Q(erp_order_id__icontains=query) |
                Q(order_display_code__icontains=query) |
                Q(customer__customer_name__icontains=query) |
                Q(customer__company_name__icontains=query) |
                Q(items__product__sku__icontains=query) |
                Q(items__product__name__icontains=query) |
                Q(parcels__parcel_code_system__icontains=query) |
                Q(parcels__tracking_number__icontains=query)
            ).distinct()

        orders_qs = orders_qs.order_by('-order_date', '-imported_at')
        paginator = Paginator(orders_qs, 30)
        try:
            orders_page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            orders_page_obj = paginator.page(1)
        except EmptyPage:
            orders_page_obj = paginator.page(paginator.num_pages if paginator.num_pages > 0 else 1)

        for order_instance in orders_page_obj.object_list:
            for item_instance in order_instance.items.all():
                item_instance.quantity_notionally_removed = order_instance.get_total_removed_quantity_for_item(item_instance.id)

        all_courier_companies_for_js = list(CourierCompany.objects.all().values('id', 'name', 'code'))
        context['all_courier_companies_json'] = json.dumps(all_courier_companies_for_js, cls=DjangoJSONEncoder)

        # --- START: New logic to get total parcel counts for displayed customers ---
        # 1. Get the unique customer IDs from the orders on the current page.
        customer_ids_on_page = {order.customer.id for order in orders_page_obj.object_list if order.customer}

        # 2. Run one efficient query to get the total parcel count for each of those customers.
        parcel_counts = Customer.objects.filter(
            id__in=customer_ids_on_page
        ).annotate(
            total_parcel_count=Count('orders__parcels')
        )

        # 3. Create a dictionary for easy lookup in the template.
        customer_parcel_counts = {
            customer.id: customer.total_parcel_count for customer in parcel_counts
        }
        # --- END: New logic ---

        context.update({
            'orders_page_obj': orders_page_obj,
            'customer_parcel_counts': customer_parcel_counts,
            'total_orders_count': paginator.count,
            'selected_warehouse': selected_warehouse_id,
            'selected_status': selected_status,
            'query': query,
            'page_title': "Customer Orders",
        })

        if is_ajax:
            # Use the specific flag for customer orders
            if fetch_dynamic_content_only_co:
                template_to_render = 'operation/partials/_customer_orders_table_with_pagination.html'
            else:
                template_to_render = 'operation/partials/customer_orders_table.html'
            response = render(request, template_to_render, context)
            response['X-Total-Orders-Count'] = paginator.count
            if orders_page_obj.has_next():
                response['HX-Trigger-After-Swap'] = 'loadMoreCustomerOrdersAvailable'
            else:
                response['HX-Trigger-After-Swap'] = 'loadMoreCustomerOrdersUnavailable'
            return response



    elif active_tab == DEFAULT_PARCELS_TAB:
        # Check for the specific parcel list fetch parameter for AJAX
        fetch_parcel_list_only = request.GET.get('fetch_parcel_list_only') == 'true'

        parcels_qs = Parcel.objects.select_related(
            'order__warehouse', 'order__imported_by', 'created_by', 'courier_company', 'billing_item'
        ).prefetch_related(
            Prefetch('items_in_parcel', queryset=ParcelItem.objects.select_related(
                'order_item__product', 'shipped_from_batch'
            ).order_by('order_item__product__name'))
        ).all()

        courier_companies_for_filter = CourierCompany.objects.all().order_by('name')
        context['courier_companies'] = courier_companies_for_filter

        warehouses_for_parcel_filters_ui = all_warehouses_qs
        actual_selected_warehouse_id_for_query_and_ui = request.GET.get('parcel_warehouse')
        selected_parcel_courier_name = request.GET.get('parcel_courier')

        start_date_str = request.GET.get('parcel_start_date')
        end_date_str = request.GET.get('parcel_end_date')

        if not user.is_superuser:
            if user.warehouse:
                warehouses_for_parcel_filters_ui = Warehouse.objects.filter(pk=user.warehouse.pk)
                parcels_qs = parcels_qs.filter(order__warehouse=user.warehouse)
                actual_selected_warehouse_id_for_query_and_ui = str(user.warehouse.pk)
            else:
                warehouses_for_parcel_filters_ui = Warehouse.objects.none()
                parcels_qs = parcels_qs.none()
                actual_selected_warehouse_id_for_query_and_ui = None
        context['warehouses'] = warehouses_for_parcel_filters_ui
        selected_parcel_status = request.GET.get('parcel_status', None)

        if selected_parcel_status:
            # This now works for ANY status passed from the buttons
            parcels_qs = parcels_qs.filter(status=selected_parcel_status)

        if user.is_superuser and actual_selected_warehouse_id_for_query_and_ui:
            parcels_qs = parcels_qs.filter(order__warehouse_id=actual_selected_warehouse_id_for_query_and_ui)

        if selected_parcel_courier_name:
            parcels_qs = parcels_qs.filter(courier_company__code=selected_parcel_courier_name)

        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                # Filter parcels created on or after the start date
                parcels_qs = parcels_qs.filter(created_at__gte=start_date)
            except ValueError:
                pass # Ignore invalid date format

        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                # To include the entire end day, filter up to the end of that day
                end_of_day = datetime.combine(end_date, time.max)
                parcels_qs = parcels_qs.filter(created_at__lte=end_of_day)
            except ValueError:
                pass # Ignore invalid date format

        parcel_query_param = request.GET.get('parcel_q', '').strip()
        page_number = request.GET.get('page', 1)
        logger.debug(f"[ParcelTab] Filters: parcel_warehouse='{actual_selected_warehouse_id_for_query_and_ui}', parcel_courier='{selected_parcel_courier_name}', parcel_q='{parcel_query_param}', page='{page_number}'")

        if parcel_query_param:
            parcels_qs = parcels_qs.filter(
                Q(parcel_code_system__icontains=parcel_query_param) |
                Q(tracking_number__icontains=parcel_query_param) |
                Q(order__erp_order_id__icontains=parcel_query_param) |
                Q(order__customer__customer_name__icontains=parcel_query_param) |
                Q(items_in_parcel__order_item__product__name__icontains=parcel_query_param) |
                Q(items_in_parcel__order_item__product__sku__icontains=parcel_query_param) |
                Q(items_in_parcel__shipped_from_batch__batch_number__icontains=parcel_query_param) |
                Q(items_in_parcel__shipped_from_batch__location_label__icontains=parcel_query_param)
            ).distinct()

        parcels_qs = parcels_qs.order_by('-created_at')
        parcel_paginator = Paginator(parcels_qs, 30)
        try:
            parcels_page = parcel_paginator.page(page_number)
        except PageNotAnInteger:
            parcels_page = parcel_paginator.page(1)
        except EmptyPage:
            parcels_page = parcel_paginator.page(parcel_paginator.num_pages if parcel_paginator.num_pages > 0 else 1)

        context.update({
            'parcels': parcels_page,
            'total_parcels_count': parcel_paginator.count,
            'selected_parcel_warehouse': actual_selected_warehouse_id_for_query_and_ui,
            'selected_parcel_courier': selected_parcel_courier_name,
            'parcel_query': parcel_query_param,
            'page_title': "Parcel Details",
            'start_date': start_date_str,
            'end_date': end_date_str,
        })

        if is_ajax:
            if fetch_parcel_list_only:
                template_to_render = 'operation/partials/_parcels_list_content.html'
            else:
                template_to_render = 'operation/partials/parcels_table.html'

            response = render(request, template_to_render, context)
            response['X-Total-Parcels-Count'] = parcel_paginator.count
            return response

    return render(request, 'operation/order_management_base.html', context)

@login_required
def load_more_customer_orders(request):
    logger.debug(f"[LoadMoreCustomerOrders] Request GET params: {request.GET}")
    user = request.user

    orders_qs = Order.objects.select_related(
        'warehouse',
        'imported_by'
    ).prefetch_related(
        Prefetch('items', queryset=OrderItem.objects.select_related('product', 'suggested_batch_item', 'warehouse_product').order_by('product__name')),
        # FIX: Added select_related('courier_company') to efficiently fetch the courier name.
        Prefetch('parcels', queryset=Parcel.objects.select_related('courier_company').order_by('-created_at'))
    ).all()

    if not user.is_superuser and user.warehouse:
        orders_qs = orders_qs.filter(warehouse=user.warehouse)
    elif not user.is_superuser: # Non-superuser with no warehouse
        orders_qs = orders_qs.none()

    selected_warehouse_id = request.GET.get('warehouse')
    selected_status = request.GET.get('status')
    query = request.GET.get('q', '').strip()
    page_number = request.GET.get('page', 1) # Default to page 1 if not provided

    if user.is_superuser and selected_warehouse_id:
        orders_qs = orders_qs.filter(warehouse_id=selected_warehouse_id)

    if selected_status:
        orders_qs = orders_qs.filter(status=selected_status)
    if query:
        orders_qs = orders_qs.filter(
            Q(erp_order_id__icontains=query) |
            Q(customer_name__icontains=query) |
            Q(items__product__sku__icontains=query)
        ).distinct()

    orders_qs = orders_qs.order_by('-order_date', '-imported_at')

    paginator = Paginator(orders_qs, 10)
    try:
        orders_page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        logger.warning(f"PageNotAnInteger for page '{page_number}' in load_more_customer_orders. Defaulting to page 1.")
        orders_page_obj = paginator.page(1)
        if not orders_page_obj.object_list:
            return HttpResponse("")
    except EmptyPage:
        logger.info(f"EmptyPage for page '{page_number}' in load_more_customer_orders. No more items.")
        return HttpResponse("")

    for order_instance in orders_page_obj.object_list:
        for item_instance in order_instance.items.all():
            item_instance.quantity_notionally_removed = order_instance.get_total_removed_quantity_for_item(item_instance.id)

    context = {
        'orders': orders_page_obj.object_list,
        'request': request,
    }

    html_rows = render_to_string('operation/partials/_customer_orders_list_items_only.html', context)

    response = HttpResponse(html_rows)
    if orders_page_obj.has_next():
        response['HX-Trigger'] = 'loadMoreCustomerOrdersAvailable'
    else:
        response['HX-Trigger'] = 'loadMoreCustomerOrdersUnavailable'
    return response


@login_required
def import_orders_from_excel(request):
    if request.method == 'POST':
        form = ExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            excel_file = request.FILES['excel_file']
            file_name_lower = excel_file.name.lower()

            try:
                # Initialize workbook and sheet variables
                workbook_data = None
                sheet = None
                is_xlsx = False # Flag to distinguish between .xlsx and .xls

                # Determine file type and load workbook
                if file_name_lower.endswith('.xlsx'):
                    workbook_data = openpyxl.load_workbook(excel_file, data_only=True) # data_only for cell values
                    sheet = workbook_data.active # Get the active sheet
                    is_xlsx = True
                elif file_name_lower.endswith('.xls'):
                    # xlrd needs file content, not path for in-memory files
                    file_contents = excel_file.read()
                    workbook_data = xlrd.open_workbook(file_contents=file_contents)
                    sheet = workbook_data.sheet_by_index(0) # Get the first sheet
                    is_xlsx = False
                else:
                    messages.error(request, "Unsupported file format. Please upload .xlsx or .xls files.")
                    return redirect('operation:order_list') # Or appropriate redirect

                # Extract headers and prepare data rows iterator
                headers = []
                data_rows_iterator = None

                if is_xlsx: # openpyxl
                    if sheet.max_row > 0: # Check if sheet has any rows
                        headers = [cell.value for cell in sheet[1]] # First row for headers
                        data_rows_iterator = sheet.iter_rows(min_row=2, values_only=True) # Data from second row
                    else: headers = [] # No rows, no headers
                else: # xlrd
                    if sheet.nrows > 0: # Check if sheet has any rows
                        headers = [sheet.cell_value(0, col_idx) for col_idx in range(sheet.ncols)] # First row
                        # Define a generator for xlrd rows to handle date conversion
                        def xlrd_rows_iterator(sheet_obj, book_datemode):
                            for r_idx in range(1, sheet_obj.nrows): # Start from second row (index 1)
                                row_values_xls = []
                                for c_idx in range(sheet_obj.ncols):
                                    cell_type = sheet_obj.cell_type(r_idx, c_idx)
                                    cell_value = sheet_obj.cell_value(r_idx, c_idx)
                                    if cell_type == xlrd.XL_CELL_DATE:
                                        # Convert Excel date number to datetime object
                                        date_tuple = xlrd.xldate_as_datetime(cell_value, book_datemode)
                                        row_values_xls.append(date_tuple)
                                    elif cell_type == xlrd.XL_CELL_NUMBER and cell_value == int(cell_value):
                                        # If number is whole, treat as int
                                        row_values_xls.append(int(cell_value))
                                    else:
                                        row_values_xls.append(cell_value)
                                yield row_values_xls
                        data_rows_iterator = xlrd_rows_iterator(sheet, workbook_data.datemode)
                    else: headers = [] # No rows, no headers

                if not headers:
                        messages.error(request, "The Excel file is empty or has no header row.")
                        return redirect('operation:order_list')

                # --- Define header mapping configuration ---
                # Keys are internal field names, values are expected Excel column headers (case-insensitive match)
                header_mapping_config = {
                    'erp_order_id': 'order id',
                    'order_date': 'order date',
                    'warehouse_name': 'warehouse name', # Critical for linking to Warehouse model
                    'customer_name': 'address name',    # For Order.customer_name
                    'company_name': 'company',
                    'address_line1': 'address',
                    'country': 'country',
                    'city': 'city',
                    'state': 'state',
                    'zip_code': 'zip',
                    'phone': 'phone',
                    'vat_number': 'vat number',
                    'product_name_from_excel': 'product name', # For OrderItem.erp_product_name and Product lookup
                    'quantity_ordered': 'product quantity',
                    'is_cold': 'iscold', # For OrderItem.is_cold_item and Order.is_cold_chain
                    'title_notes': 'title', # For Order.title_notes
                    'shipping_notes': 'comment', # For Order.shipping_notes
                }

                # Normalize actual headers from file (lowercase, strip whitespace)
                normalized_actual_headers = {str(h).strip().lower(): str(h).strip() for h in headers if h is not None}

                # Map internal keys to actual header names found in the file
                header_map_for_indexing = {}
                missing_headers_from_config = []
                # Define which internal keys are absolutely critical for basic processing
                critical_internal_keys = ['erp_order_id', 'order_date', 'warehouse_name', 'product_name_from_excel', 'quantity_ordered']

                for internal_key, excel_header_normalized_target in header_mapping_config.items():
                    found_actual_header = None
                    # Find the original case header from the file that matches the normalized target
                    for actual_header_normalized, actual_header_original_case in normalized_actual_headers.items():
                        if actual_header_normalized == excel_header_normalized_target.lower():
                            found_actual_header = actual_header_original_case
                            break
                    if found_actual_header:
                        header_map_for_indexing[internal_key] = found_actual_header
                    elif internal_key in critical_internal_keys: # If a critical header is missing
                            missing_headers_from_config.append(f"'{excel_header_normalized_target}' (expected for '{internal_key}')")


                if missing_headers_from_config:
                    messages.error(request, f"Required headers not found in Excel: {', '.join(missing_headers_from_config)}. Available headers found: {', '.join(filter(None,headers))}")
                    return redirect('operation:order_list')

                # Create a map from internal key to column index in the actual file
                final_header_to_index_map = {}
                for internal_key, mapped_excel_header_original_case in header_map_for_indexing.items():
                    try:
                        # Find index of the original case header in the original headers list
                        idx = headers.index(mapped_excel_header_original_case)
                        final_header_to_index_map[internal_key] = idx
                    except ValueError:
                        # This should not happen if previous mapping was successful, but as a safeguard
                        messages.error(request, f"Configuration error: Mapped Excel header '{mapped_excel_header_original_case}' (for internal key '{internal_key}') not found in the original headers list. This is an internal logic error.")
                        return redirect('operation:order_list')


                # --- Process rows ---
                orders_data = {} # To group items by order_id
                last_valid_erp_order_id = None # To handle items for the same order on subsequent rows if Order ID is blank

                for row_idx, row_tuple in enumerate(data_rows_iterator, start=2): # row_idx is 1-based for messages
                    # Skip entirely blank rows
                    if not any(str(cell_val).strip() for cell_val in row_tuple if cell_val is not None):
                        logger.debug(f"Row {row_idx}: Skipped. Entirely empty or whitespace.")
                        continue

                    # Helper to get cell value by internal key, handling missing columns and data types
                    def get_current_row_value(internal_key_lambda, default=None):
                        idx = final_header_to_index_map.get(internal_key_lambda)
                        if idx is not None and idx < len(row_tuple) and row_tuple[idx] is not None:
                            val_lambda = row_tuple[idx]
                            # If already datetime (from xlrd) or number, return as is
                            if isinstance(val_lambda, (datetime.datetime, datetime.date)): # ✅ CORRECT
                                return val_lambda
                            # Otherwise, convert to string and strip
                            val_str = str(val_lambda).strip()
                            return val_str if val_str != "" else default # Return default if stripped string is empty
                        return default

                    # Get Order ID for current row, or use last valid one if current is blank
                    current_row_erp_order_id = get_current_row_value('erp_order_id')
                    erp_order_id_to_use = current_row_erp_order_id if current_row_erp_order_id else last_valid_erp_order_id

                    if not erp_order_id_to_use:
                        messages.warning(request, f"Row {row_idx}: Skipped. Missing Order ID and no previous order context.")
                        logger.warning(f"Row {row_idx}: Skipped. Missing Order ID.")
                        continue # Skip row if no Order ID can be determined

                    if current_row_erp_order_id: # If this row has an Order ID, it becomes the new context
                        last_valid_erp_order_id = current_row_erp_order_id
                    erp_order_id_to_use = str(erp_order_id_to_use) # Ensure it's a string for dict key

                    # Get critical item data
                    product_name_excel = get_current_row_value('product_name_from_excel')
                    quantity_ordered_str = get_current_row_value('quantity_ordered')

                    if not all([product_name_excel, quantity_ordered_str]):
                        messages.warning(request, f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Missing critical item data (Product Name or Quantity). Skipping item.")
                        logger.warning(f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Missing Product Name ('{product_name_excel}') or Qty ('{quantity_ordered_str}'). Skipping item.")
                        continue # Skip this item if essential item data is missing

                    # If this is the first time we see this Order ID, parse order-level details
                    order_date_for_this_entry = None # Initialize for current row context
                    if erp_order_id_to_use not in orders_data:
                        # Get order-level details (only needed once per order)
                        order_date_val = get_current_row_value('order_date')
                        warehouse_name = get_current_row_value('warehouse_name')
                        customer_name = get_current_row_value('customer_name') # Mapped from 'address name'

                        # Parse order_date (handle various formats and types)
                        if order_date_val:
                            # Correctly check for datetime or date types first
                            if isinstance(order_date_val, (datetime.datetime, datetime.date)):
                                # If it's a datetime object, get the date part. If it's a date, this does nothing.
                                order_date_for_this_entry = order_date_val.date() if hasattr(order_date_val, 'date') else order_date_val

                            elif isinstance(order_date_val, str):
                                # Initialize to None
                                order_date_for_this_entry = None
                                # First, try Django's robust parser. It handles ISO formats like YYYA-MM-DD well.
                                parsed_by_django = parse_date(order_date_val)
                                if parsed_by_django:
                                    if isinstance(parsed_by_django, datetime.datetime):
                                        order_date_for_this_entry = parsed_by_django.date()
                                    else:
                                        order_date_for_this_entry = parsed_by_django

                                # If Django's parser fails, try our specific list of formats
                                if not order_date_for_this_entry:
                                    for fmt in ('%b %d, %Y', '%B %d, %Y', '%d/%m/%Y', '%m/%d/%Y', '%Y%m%d'):
                                        try:
                                            parsed_date = datetime.datetime.strptime(order_date_val, fmt)
                                            order_date_for_this_entry = parsed_date.date()
                                            break # Success, exit loop
                                        except ValueError:
                                            continue # Try next format

                            # Handle Excel's numeric date format (for .xls files)
                            elif isinstance(order_date_val, (float, int)) and hasattr(workbook_data, 'datemode') and not is_xlsx:
                                try:
                                    order_date_for_this_entry = xlrd.xldate_as_datetime(order_date_val, workbook_data.datemode).date()
                                except (ValueError, TypeError):
                                    pass # Will be caught by the check below

                        # After all attempts, if it's still not a valid date, log a warning.
                        if not order_date_for_this_entry and order_date_val:
                            logger.warning(f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Unparseable order_date '{order_date_val}'.")
                        # --- START: New Customer Logic Integration ---
                        # 1. Collect all customer info from the row
                        customer_address_details = {
                            'address_line1': get_current_row_value('address_line1', ''),
                            'city': get_current_row_value('city', ''),
                            'state': get_current_row_value('state', ''),
                            'zip_code': get_current_row_value('zip_code', ''),
                            'country': get_current_row_value('country', ''),
                        }

                        # 2. Find or create the customer record using the utility function
                        customer_obj, created = get_or_create_customer_from_import(
                            customer_name=get_current_row_value('customer_name', ''),
                            company_name=get_current_row_value('company_name', ''),
                            phone_number=get_current_row_value('phone', ''),
                            address_info=customer_address_details,
                            vat_number=get_current_row_value('vat_number', '')
                        )
                        if created:
                            logger.info(f"Import created new customer: {customer_obj.customer_name} ({customer_obj.customer_id})")
                        # --- END: New Customer Logic Integration ---

                        # Check if critical order-level details are present for a new order entry
                        if not all([order_date_for_this_entry, warehouse_name, customer_name]):
                            error_parts = []
                            if not order_date_for_this_entry: error_parts.append("Order Date (missing or invalid format)")
                            if not warehouse_name: error_parts.append("Warehouse Name")
                            if not customer_name: error_parts.append("Customer Name (Address Name)")
                            msg = f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Missing essential order details for first line: {', '.join(error_parts)}. Skipping order."
                            messages.warning(request, msg)
                            logger.warning(msg)
                            last_valid_erp_order_id = None # Reset context as this order is invalid
                            continue # Skip to next row

                        # Store order details
                        orders_data[erp_order_id_to_use] = {
                            'order_details': {
                                'customer_obj': customer_obj, # Store the actual object
                                'erp_order_id': erp_order_id_to_use,
                                'order_date': order_date_for_this_entry,
                                'warehouse_name': get_current_row_value('warehouse_name'),
                                'is_cold_chain': False,
                                'title_notes': get_current_row_value('title_notes'),
                                'shipping_notes': get_current_row_value('shipping_notes'),
                            },
                            'items': []
                        }

                    # Process item quantity
                    try:
                        quantity_ordered = int(float(str(quantity_ordered_str))) # Convert to float first, then int
                        if quantity_ordered <= 0:
                            raise ValueError("Quantity must be positive.")
                    except (ValueError, TypeError):
                        msg = f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Invalid quantity '{quantity_ordered_str}' for item '{product_name_excel}'. Skipping item."
                        messages.warning(request, msg)
                        logger.warning(msg)
                        continue # Skip this item

                    # Process is_cold
                    is_cold_str = get_current_row_value('is_cold')
                    is_cold = str(is_cold_str).strip().lower() == 'yes' if is_cold_str else False

                    if is_cold: # If any item is cold, mark the whole order as cold chain
                        orders_data[erp_order_id_to_use]['order_details']['is_cold_chain'] = True

                    # Add item to the order's item list
                    orders_data[erp_order_id_to_use]['items'].append({
                        'product_name_from_excel': str(product_name_excel), # Ensure string
                        'quantity_ordered': quantity_ordered,
                        'is_cold_item': is_cold,
                        # Add other item-specific fields here if needed
                    })
                # --- End of row processing loop ---

                # --- Database Operations (after parsing all rows) ---
                with transaction.atomic():
                    created_orders_count = 0
                    updated_orders_count = 0 # If we decide to update existing orders
                    created_items_count = 0
                    skipped_orders_db = 0 # Orders skipped due to DB issues (e.g., Warehouse not found)
                    skipped_items_db = 0  # Items skipped due to DB issues (e.g., Product not found)

                    for erp_id_key, data_dict in orders_data.items():
                        order_details_map = data_dict['order_details']
                        try:
                            # Get Warehouse object
                            warehouse = Warehouse.objects.get(name__iexact=order_details_map['warehouse_name'])
                        except Warehouse.DoesNotExist:
                            messages.error(request, f"Order ID {erp_id_key}: Warehouse '{order_details_map['warehouse_name']}' not found during DB operations. Skipping order.")
                            logger.error(f"Order ID {erp_id_key}: Warehouse '{order_details_map['warehouse_name']}' not found.")
                            skipped_orders_db += 1
                            continue # Skip this whole order

                        # Prepare fields for Order model instance, excluding those used for lookup or processed separately
                        order_field_defaults = {
                            'customer': order_details_map['customer_obj'], # <-- Use the customer object
                            'order_date': order_details_map['order_date'],
                            'warehouse': warehouse,
                            'is_cold_chain': order_details_map['is_cold_chain'],
                            'title_notes': order_details_map['title_notes'],
                            'shipping_notes': order_details_map['shipping_notes'],
                            'status': 'NEW_ORDER',
                            'imported_by': request.user if request.user.is_authenticated else None
                        }

                        # Ensure erp_order_id is a string for consistency
                        current_order_erp_id_str = str(order_details_map['erp_order_id'])

                        # Using update_or_create to handle both new and existing orders
                        # If order exists, its items will be replaced.
                        order, created = Order.objects.update_or_create(
                            erp_order_id=current_order_erp_id_str, # Lookup field
                            defaults=order_field_defaults
                        )
                        # Ensure erp_order_id is set correctly even if it was the lookup key
                        # (update_or_create doesn't update the lookup key itself via defaults)
                        order.erp_order_id = current_order_erp_id_str # Redundant if created, but ensures if updated.

                        if created:
                            created_orders_count += 1
                        else:
                            # If order existed, clear its previous items to replace with new ones from file
                            order.items.all().delete()
                            updated_orders_count +=1 # Count as updated

                        current_order_items_processed_db = 0
                        for item_data in data_dict['items']:
                            product_identifier_from_excel = item_data['product_name_from_excel']
                            product = None
                            try:
                                # Try SKU first (case-insensitive)
                                product = Product.objects.get(sku__iexact=product_identifier_from_excel)
                            except Product.DoesNotExist:
                                # If SKU not found, try Name (case-insensitive)
                                try:
                                    product = Product.objects.get(name__iexact=product_identifier_from_excel)
                                except Product.DoesNotExist:
                                    messages.error(request, f"Order ID {erp_id_key}, Item Identifier '{product_identifier_from_excel}': Product not found by SKU or Name. Skipping item.")
                                    logger.error(f"Order ID {erp_id_key}, Item '{product_identifier_from_excel}': Product not found by SKU/Name.")
                                    skipped_items_db +=1
                                    continue # Skip this item
                                except Product.MultipleObjectsReturned:
                                    messages.error(request, f"Order ID {erp_id_key}, Item Name '{product_identifier_from_excel}': Multiple products found by this name. Use unique SKU or ensure unique names. Skipping item.")
                                    logger.error(f"Order ID {erp_id_key}, Item '{product_identifier_from_excel}': Multiple products by name.")
                                    skipped_items_db +=1
                                    continue # Skip this item
                            except Product.MultipleObjectsReturned:
                                messages.error(request, f"Order ID {erp_id_key}, SKU '{product_identifier_from_excel}': Multiple products found with this SKU. SKUs must be unique. Skipping item.")
                                logger.error(f"Order ID {erp_id_key}, SKU '{product_identifier_from_excel}': Multiple products by SKU.")
                                skipped_items_db +=1
                                continue # Skip this item

                            # Get or create WarehouseProduct link
                            warehouse_product_instance = WarehouseProduct.objects.filter(product=product, warehouse=warehouse).first()
                            if not warehouse_product_instance:
                                # If you want to auto-create WarehouseProduct if it doesn't exist:
                                # warehouse_product_instance = WarehouseProduct.objects.create(product=product, warehouse=warehouse, quantity=0, threshold=0)
                                # logger.info(f"Auto-created WarehouseProduct for {product.sku} @ {warehouse.name}")
                                # For now, let's assume it must exist or item is added without this specific link
                                messages.warning(request, f"Order ID {erp_id_key}, Item '{product.sku}': WarehouseProduct link not found for warehouse '{warehouse.name}'. Item added without specific stock link.")
                                logger.warning(f"Order ID {erp_id_key}, Item '{product.sku}': WarehouseProduct link not found for WH '{warehouse.name}'.")

                            # Create OrderItem
                            oi_defaults = {
                                'quantity_ordered': item_data['quantity_ordered'],
                                'erp_product_name': product_identifier_from_excel, # Store the name from Excel
                                'is_cold_item': item_data.get('is_cold_item', False),
                                'status': 'PENDING_PROCESSING', # Default status for new items
                                'warehouse_product': warehouse_product_instance # Can be None if not found
                            }
                            OrderItem.objects.create(order=order, product=product, **oi_defaults)
                            current_order_items_processed_db +=1

                        created_items_count += current_order_items_processed_db
                        order.save() # Save order again to trigger any on_save logic if item changes affect order


                # --- Summarize results ---
                final_message_parts = []
                if created_orders_count > 0: final_message_parts.append(f"Orders Created: {created_orders_count}")
                if updated_orders_count > 0: final_message_parts.append(f"Orders Updated: {updated_orders_count}")
                if created_items_count > 0: final_message_parts.append(f"Order Items Processed: {created_items_count}")

                if not final_message_parts and (skipped_orders_db == 0 and skipped_items_db == 0):
                    # This means no new orders were created, no existing orders were updated (based on erp_order_id),
                    # and no items were processed (likely because all orders in file already existed and had no changes,
                    # or the file was empty of valid data after parsing).
                    final_message_parts.append("No new orders or items to import based on ERP IDs. Existing orders might have been updated if their content changed.")

                if skipped_orders_db > 0: final_message_parts.append(f"Orders Skipped (DB): {skipped_orders_db}")
                if skipped_items_db > 0: final_message_parts.append(f"Items Skipped (DB): {skipped_items_db}")

                if final_message_parts:
                    messages.success(request, "Import process complete. " + ", ".join(final_message_parts) + ".")
                else:
                    # This case implies the file was parsed but contained no data rows that led to any action or skip.
                    messages.info(request, "No data found in the Excel file to process orders.")


            except ValueError as ve: # Catch errors during parsing or data validation before DB
                messages.error(request, f"Error in file structure or critical content: {str(ve)}")
                logger.error(f"Import ValueError: {str(ve)}", exc_info=True)
            except xlrd.XLRDError as xe: # Specifically for .xls reading errors
                messages.error(request, f"Error reading .xls file. It might be corrupted or an incompatible version: {str(xe)}")
                logger.error(f"Import XLRDError: {str(xe)}", exc_info=True)
            except Exception as e: # Catch-all for other unexpected errors during processing
                messages.error(request, f"An unexpected error occurred during the import process: {str(e)}")
                logger.error(f"Import Exception: {str(e)}", exc_info=True)

            return redirect('operation:order_list') # Redirect back to the order list view
        else: # Form is not valid (e.g., no file uploaded)
            # Django messages framework will display form.errors if you render the form
            # For now, let's add a generic message if needed, or rely on form rendering
            for field, errors_list in form.errors.items():
                for error in errors_list:
                    messages.error(request, f"Error in field '{form.fields[field].label if field != '__all__' else 'Form'}': {error}")
    # If not POST, or if form was invalid and we didn't redirect (e.g. no file),
    # redirecting to order_list is a safe default.
    # The order_list view will render the import_form again.
    return redirect('operation:order_list')


@login_required
def get_order_items_for_packing(request, order_pk):
    """
    Gets all data needed to populate the 'Pack Order' modal.
    This version now always includes all active couriers in the daily counts,
    showing 0 for those with no parcels today.
    """
    try:
        order = get_object_or_404(
            Order.objects.prefetch_related(
                Prefetch(
                    'items',
                    queryset=OrderItem.objects.filter(
                        Q(status='PENDING_PROCESSING') | (Q(status='PACKED') & Q(quantity_packed__lt=F('quantity_ordered')))
                    ).select_related('product', 'warehouse_product')
                )
            ).select_related('warehouse'),
            pk=order_pk
        )

        if not request.user.is_superuser and (not request.user.warehouse or order.warehouse != request.user.warehouse):
            return JsonResponse({'success': False, 'message': 'Permission denied for this order.'}, status=403)

        # Prepare initial data for the packing formset
        initial_form_data = []
        for item in order.items.all():
            total_removed_for_this_item = order.get_total_removed_quantity_for_item(item.id)
            quantity_remaining_to_pack_for_this_item = item.quantity_ordered - item.quantity_packed - total_removed_for_this_item

            if quantity_remaining_to_pack_for_this_item > 0:
                best_suggested_batch = get_suggested_batch_for_order_item(item, quantity_remaining_to_pack_for_this_item)
                initial_form_data.append({
                    'order_item_id': item.pk,
                    'product_name': item.product.name if item.product else item.erp_product_name,
                    'sku': item.product.sku if item.product else "N/A",
                    'quantity_to_pack': quantity_remaining_to_pack_for_this_item,
                    'selected_batch_item_id': best_suggested_batch.pk if best_suggested_batch else None,
                })

        formset_html_content = ""
        message_for_modal = ""
        if not initial_form_data:
            message_for_modal = 'All items for this order are already fully packed or have no remaining quantity.'
            formset_html_content = f'<p class="text-center py-4 text-gray-500">{message_for_modal}</p>'
        else:
            packing_items_formset = InitialParcelItemFormSet(initial=initial_form_data, prefix='packitems')
            formset_html_content = render_to_string(
                'operation/partials/pack_order_formset.html',
                {'formset': packing_items_formset, 'order': order},
                request=request
            )

        env_type = 'COLD' if order.is_cold_chain else 'AMBIENT'
        available_packaging = list(PackagingType.objects.filter(
            environment_type=env_type, is_active=True
        ).values('id', 'name', 'type_code'))

        # --- REVISED LOGIC FOR "ALWAYS-ON" COURIER DASHBOARD ---
        # 1. Get today's actual parcel counts
        now = timezone.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        todays_parcel_counts_query = Parcel.objects.filter(
            created_at__range=(start_of_day, end_of_day),
            courier_company__isnull=False
        ).values('courier_company__name').annotate(count=Count('id'))

        todays_parcel_counts = {
            entry['courier_company__name']: entry['count']
            for entry in todays_parcel_counts_query
        }

        # 2. Get all active couriers
        active_couriers = CourierCompany.objects.filter(is_active=True).order_by('name')

        # 3. Combine them, ensuring all active couriers are present with a count (defaulting to 0)
        final_daily_courier_counts = {
            courier.name: todays_parcel_counts.get(courier.name, 0)
            for courier in active_couriers
        }

        # Prepare list of courier details for the main dropdown
        courier_list_for_modal = [
            {'id': c.id, 'name': c.name, 'code': c.code or ''}
            for c in active_couriers
        ]

        customer_name_display = order.customer.customer_name if order.customer else "N/A"

        return JsonResponse({
            'success': True,
            'order_id': order.pk,
            'erp_order_id': order.erp_order_id,
            'customer_name': customer_name_display,
            'formset_html': formset_html_content,
            'message': message_for_modal,
            'shipping_notes_for_parcel': order.shipping_notes or '',
            'is_cold_chain': order.is_cold_chain,
            'daily_courier_counts_object': final_daily_courier_counts, # Send the final, complete list
            'available_couriers': courier_list_for_modal,
            'available_packaging': available_packaging
        })

    except Http404:
        logger.warning(f"Order PK {order_pk} not found in get_order_items_for_packing.")
        return JsonResponse({'success': False, 'message': 'Order not found.'}, status=404)
    except Exception as e:
        logger.error(f"Unexpected error in get_order_items_for_packing for order_pk {order_pk}: {e}\n{traceback.format_exc()}")
        return JsonResponse({'success': False, 'message': 'An unexpected server error occurred while preparing packing information.'}, status=500)

@login_required
@require_POST # Ensures this view only accepts POST requests
@transaction.atomic # Ensures all database operations are atomic
def process_packing_for_order(request, order_pk):
    """
    Processes the packing form submission to create a new Parcel,
    update stock levels, and update the parent Order status.
    This is the complete, updated version.
    """
    try:
        order = get_object_or_404(Order.objects.select_related('warehouse'), pk=order_pk)

        # 1. Permission Check
        if not request.user.is_superuser and (not request.user.warehouse or order.warehouse != request.user.warehouse):
            logger.warning(f"Permission denied for order {order_pk} by user {request.user.username}.")
            return JsonResponse({'success': False, 'message': 'Permission denied for this order.'}, status=403)

        # 2. Get and Sanitize Form Data
        parcel_notes_from_form = request.POST.get('parcel-notes', order.shipping_notes or '')
        # MODIFIED: Get Courier ID from the form
        courier_id_from_form = request.POST.get('parcel-courier_id', '').strip()
        packaging_type_id_from_form = request.POST.get('parcel-packaging_type', '').strip()

        # 3. Validate Form Data
        if not courier_id_from_form or not courier_id_from_form.isdigit():
            return JsonResponse({'success': False, 'message': 'Courier selection is required.'}, status=400)
        if not packaging_type_id_from_form or not packaging_type_id_from_form.isdigit():
            return JsonResponse({'success': False, 'message': 'Packaging selection is required.'}, status=400)

        # Fetch related objects
        courier_instance = get_object_or_404(CourierCompany, pk=courier_id_from_form)
        packaging_type = get_object_or_404(PackagingType, pk=packaging_type_id_from_form)

        # 4. Process Packed Items from the Formset
        num_item_forms = int(request.POST.get('packitems-TOTAL_FORMS', 0))
        items_to_pack_data = []
        any_item_actually_packed_this_session = False

        for i in range(num_item_forms):
            qty_to_pack_str = request.POST.get(f'packitems-{i}-quantity_to_pack')
            if not qty_to_pack_str or int(qty_to_pack_str) <= 0:
                continue

            any_item_actually_packed_this_session = True
            order_item_id_str = request.POST.get(f'packitems-{i}-order_item_id')
            batch_id_str = request.POST.get(f'packitems-{i}-selected_batch_item_id')

            try:
                order_item = OrderItem.objects.get(pk=int(order_item_id_str), order=order)
                qty_to_pack = int(qty_to_pack_str)
                if not batch_id_str or not batch_id_str.isdigit():
                    return JsonResponse({'success': False, 'message': f"A batch selection is required for {order_item.product.sku}."}, status=400)
                batch_item = InventoryBatchItem.objects.get(pk=int(batch_id_str))

                # Validation checks for the selected batch
                if batch_item.warehouse_product.warehouse != order.warehouse:
                    return JsonResponse({'success': False, 'message': f"Batch for {order_item.product.sku} is from a different warehouse than the order."}, status=400)
                if batch_item.warehouse_product != order_item.warehouse_product:
                    return JsonResponse({'success': False, 'message': f"Product mismatch for batch {batch_item.batch_number}."}, status=400)
                if qty_to_pack > batch_item.quantity:
                    return JsonResponse({'success': False, 'message': f"Not enough stock in batch {batch_item.batch_number}."}, status=400)

                total_removed_for_item = order.get_total_removed_quantity_for_item(order_item.id)
                quantity_remaining_on_order_item = (order_item.quantity_ordered - total_removed_for_item) - order_item.quantity_packed
                if qty_to_pack > quantity_remaining_on_order_item:
                    return JsonResponse({'success': False, 'message': f"Cannot pack {qty_to_pack} of {order_item.product.sku}. Only {quantity_remaining_on_order_item} left."}, status=400)

                items_to_pack_data.append({'order_item': order_item, 'quantity': qty_to_pack, 'batch': batch_item})
            except (OrderItem.DoesNotExist, InventoryBatchItem.DoesNotExist, ValueError) as e:
                logger.error(f"Error processing item data for packing order {order_pk}: {e}", exc_info=True)
                return JsonResponse({'success': False, 'message': f'Invalid item data submitted: {str(e)}'}, status=400)

        if not any_item_actually_packed_this_session:
            return JsonResponse({'success': False, 'message': 'No items were specified with a quantity greater than 0.'}, status=400)

        # 5. Create the Parcel
        new_parcel = Parcel.objects.create(
            order=order,
            created_by=request.user,
            notes=parcel_notes_from_form,
            courier_company=courier_instance,  # MODIFIED: Assign the CourierCompany object
            packaging_type=packaging_type
        )
        logger.info(f"Parcel {new_parcel.pk} ({new_parcel.parcel_code_system}) created for order {order_pk}.")

        # 6a. Create ParcelItems and Update Item Stock
        for item_data in items_to_pack_data:
            oi = item_data['order_item']
            batch = item_data['batch']
            qty = item_data['quantity']
            ParcelItem.objects.create(
                parcel=new_parcel,
                order_item=oi,
                quantity_shipped_in_this_parcel=qty,
                shipped_from_batch=batch
            )
            batch.quantity = F('quantity') - qty
            batch.save(update_fields=['quantity'])

            StockTransaction.objects.create(
                warehouse=batch.warehouse_product.warehouse,
                transaction_type=StockTransaction.TransactionTypes.SALE_PACKED_OUT,
                warehouse_product=batch.warehouse_product,
                product=batch.warehouse_product.product,
                batch_item_involved=batch,
                quantity=-qty,
                reference_note=f"LWA Order {order.erp_order_id}, Parcel {new_parcel.parcel_code_system}, Batch {batch.batch_number}",
                related_order=order,
                recorded_by=request.user
            )

        # 6b. Deduct Packaging Material Stock
        packaging_components = PackagingTypeMaterialComponent.objects.filter(packaging_type=packaging_type)
        for component in packaging_components:
            material = component.packaging_material
            quantity_to_deduct = component.quantity
            material.refresh_from_db()
            if material.current_stock < quantity_to_deduct:
                raise Exception(f"Not enough stock for packaging material: '{material.name}'. Required: {quantity_to_deduct}, Available: {material.current_stock}")

            material.current_stock = F('current_stock') - quantity_to_deduct
            material.save(update_fields=['current_stock'])

        # 7. Update Order Status
        order.update_status_based_on_items_and_parcels()

        messages.success(request, f"Parcel {new_parcel.parcel_code_system} created successfully for order {order.erp_order_id}.")
        return JsonResponse({
            'success': True,
            'message': f'Parcel {new_parcel.parcel_code_system} created successfully.',
            'redirect_url': reverse('operation:order_list') + f"?tab={DEFAULT_CUSTOMER_ORDERS_TAB}"
        })

    except Exception as e:
        logger.error(f"Critical error in process_packing_for_order for order_pk {order_pk}: {e}\n{traceback.format_exc()}")
        return JsonResponse({'success': False, 'message': f'An unexpected server error occurred: {str(e)}'}, status=500)


@login_required
def get_available_batches_for_order_item(request, order_item_pk):
    try:
        order_item = get_object_or_404(OrderItem.objects.select_related('product', 'warehouse_product__warehouse', 'order__warehouse'), pk=order_item_pk)

        logger.info(f"[get_available_batches] Called for OI PK: {order_item_pk}. WP ID: {order_item.warehouse_product_id if order_item.warehouse_product else 'None'}")

        # Permission Check: User must be superuser or assigned to the order's warehouse
        if not request.user.is_superuser and \
           (not request.user.warehouse or order_item.order.warehouse != request.user.warehouse):
            logger.error(f"Permission denied for user {request.user.id} on OI {order_item_pk} in get_available_batches. User WH: {request.user.warehouse}, Order WH: {order_item.order.warehouse}")
            return JsonResponse({'success': False, 'message': 'Permission denied to access batches for this order item.'}, status=403)

        if not order_item.warehouse_product:
            logger.warning(f"OrderItem {order_item.pk} (Product: {order_item.product.sku if order_item.product else 'N/A'}) has no linked WarehouseProduct. Cannot fetch batches.")
            return JsonResponse({'success': True, 'batches': [], 'message': 'Order item is not linked to a specific warehouse product.'})

        warehouse_product_for_item = order_item.warehouse_product
        today = timezone.now().date()

        logger.info(f"Fetching batches for WP: {warehouse_product_for_item.id} (Product: {warehouse_product_for_item.product.sku}), Criteria: Qty > 0, Not Expired (Today: {today})")

        # Query for available batches
        batches_qs = InventoryBatchItem.objects.filter(
            warehouse_product=warehouse_product_for_item,
            quantity__gt=0 # Only batches with stock
        ).exclude(
            expiry_date__isnull=False, expiry_date__lt=today # Exclude expired batches
        ).order_by(
            F('pick_priority').asc(nulls_last=True), # Default/Secondary picks first
            F('expiry_date').asc(nulls_last=True),   # Then FEFO (First-Expiry, First-Out)
            'date_received'                          # Then by received date as a tie-breaker
        )

        logger.info(f"Found {batches_qs.count()} total batches matching quantity/expiry for WP {warehouse_product_for_item.id} before serialization for OI {order_item.pk}.")


        batches_data = []
        for batch in batches_qs:
            priority_label = ""
            if batch.pick_priority == 0: priority_label = " [Default]"
            elif batch.pick_priority == 1: priority_label = " [Secondary]"

            location_display = f"[{batch.location_label}]" if batch.location_label else "NoLoc"
            batch_display = f"Batch: {batch.batch_number}" if batch.batch_number else "NoBatch" # Ensure batch_number is shown
            expiry_display = f"Exp: {batch.expiry_date.strftime('%d/%m/%y')}" if batch.expiry_date else "NoExp"
            qty_display = f"Qty: {batch.quantity}"

            batch_data_entry = {
                'id': batch.pk,
                'display_name': f"{location_display} | {batch_display} | {expiry_display} | {qty_display}{priority_label}",
                'quantity_available': batch.quantity,
                'pick_priority': batch.pick_priority # Send pick_priority for potential JS logic
            }
            batches_data.append(batch_data_entry)
            logger.debug(f"Added batch to dropdown data: ID {batch.pk}, Prio {batch.pick_priority}, Qty {batch.quantity}, Label: ...{priority_label}")


        if not batches_data:
            logger.info(f"No available batches were serialized for OI {order_item.pk} (WP: {warehouse_product_for_item.id}). Dropdown will indicate no batches.")
            return JsonResponse({'success': True, 'batches': [], 'message': 'No available stock batches found for this item.'})

        logger.info(f"Successfully prepared {len(batches_data)} batch options for OI {order_item.pk} dropdown.")
        return JsonResponse({'success': True, 'batches': batches_data})

    except Http404:
        logger.error(f"[get_available_batches] OrderItem PK {order_item_pk} not found.")
        return JsonResponse({'success': False, 'message': 'Order item not found.'}, status=404)
    except Exception as e:
        logger.error(f"[get_available_batches] Unexpected error for OI PK {order_item_pk}: {str(e)}", exc_info=True)
        return JsonResponse({'success': False, 'message': f'An unexpected server error occurred: {str(e)}'}, status=500)

@login_required
def get_order_items_for_editing(request, order_pk):
    # This view remains largely the same as provided by the user.
    # Ensure it correctly calculates balance_quantity_to_pack for the form.
    logger.debug(f"--- get_order_items_for_editing for order_pk: {order_pk} ---")
    order = get_object_or_404(Order.objects.prefetch_related(
        Prefetch('items', queryset=OrderItem.objects.select_related('product').order_by('product__name'))
    ), pk=order_pk)
    logger.debug(f"Order found: {order.erp_order_id}, Status: {order.get_status_display()}")

    if not request.user.is_superuser and (not request.user.warehouse or order.warehouse != request.user.warehouse):
        logger.warning(f"Permission denied for user {request.user.email} to edit order {order_pk}")
        return JsonResponse({'success': False, 'message': 'Permission denied for this order.'}, status=403)

    if order.status != 'PARTIALLY_SHIPPED': # Only allow editing for partially shipped orders
        logger.info(f"Order {order_pk} status is '{order.get_status_display()}', cannot edit items for removal.")
        return JsonResponse({'success': False, 'message': f'Order status is "{order.get_status_display()}", cannot edit items for removal.'}, status=400)

    initial_form_data = []
    logger.debug(f"Processing order items for order {order.erp_order_id}:")
    order_items_all = order.items.all() # Get all items for this order
    logger.debug(f"Total items in order: {order_items_all.count()}")

    for item in order_items_all:
        total_removed_for_this_item = order.get_total_removed_quantity_for_item(item.id)
        # Balance is ordered - packed - (already notionally removed)
        balance_qty = item.quantity_ordered - item.quantity_packed - total_removed_for_this_item

        logger.debug(f"  Item PK: {item.pk}, Prod: {item.product.sku if item.product else 'N/A'}, "
                     f"Ordered: {item.quantity_ordered}, Packed: {item.quantity_packed}, "
                     f"Total Removed Logged: {total_removed_for_this_item}, Calculated Balance: {balance_qty}")

        if balance_qty > 0: # Only include items that still have a balance to be packed/accounted for
            initial_form_data.append({
                'order_item_id': item.pk,
                'product_name': item.product.name if item.product else item.erp_product_name,
                'sku': item.product.sku if item.product else "N/A",
                # 'balance_quantity_to_pack' will be set when creating form instances
            })
            logger.debug(f"    -> ADDED to initial_form_data (PK: {item.pk}, Balance: {balance_qty})")
        else:
            logger.debug(f"    -> SKIPPED for initial_form_data (PK: {item.pk}, Balance: {balance_qty})")


    # Prepare initial data for the formset, including the calculated balance
    formset_initial_with_balance = []
    if not initial_form_data:
        logger.debug("initial_form_data is EMPTY. No items will be shown in the formset.")
    else:
        logger.debug("Populating formset_initial_with_balance...")
        for item_data_initial in initial_form_data:
            # Find the corresponding OrderItem instance to get its current state
            oi = next((i for i in order_items_all if i.pk == item_data_initial['order_item_id']), None)
            if not oi:
                logger.error(f"Could not find OrderItem with PK {item_data_initial['order_item_id']} in prefetched items.")
                continue # Should not happen if initial_form_data was built correctly

            total_removed_for_oi = order.get_total_removed_quantity_for_item(oi.id)
            balance_for_form = oi.quantity_ordered - oi.quantity_packed - total_removed_for_oi
            logger.debug(f"  For Form (OI PK: {oi.pk}): balance_for_form = {balance_for_form}")

            formset_initial_with_balance.append({
                **item_data_initial, # Includes order_item_id, product_name, sku
                'balance_quantity_to_pack': balance_for_form # This will be used by the form's __init__
            })

    logger.debug(f"Final formset_initial_with_balance count: {len(formset_initial_with_balance)}")

    edit_items_formset = RemoveOrderItemFormSet(initial=formset_initial_with_balance, prefix='edititems')
    logger.debug(f"edit_items_formset created. Number of forms: {len(edit_items_formset.forms)}")

    # Log if no forms are generated (e.g., all items are fully packed or accounted for)
    if not edit_items_formset.forms:
        logger.debug("No forms in edit_items_formset. formset_html will likely be empty or show 'no items'.")


    formset_template_name = 'operation/partials/edit_order_formset.html'
    logger.debug(f"Rendering template: {formset_template_name}")
    formset_html_content = render_to_string(
        formset_template_name,
        {'formset': edit_items_formset, 'order': order}, # Pass order for context if template needs it
        request=request
    )

    logger.debug(f"Length of rendered formset_html_content: {len(formset_html_content)}")
    if len(formset_html_content) < 200: # Arbitrary short length to log more detail
        logger.debug(f"Short formset_html_content: '{formset_html_content[:500]}...'")


    # Get the log of previously removed items for display
    removed_items_log_display = order.items_removed_log or [] # Ensure it's a list
    logger.debug(f"Removed items log being sent to client: {removed_items_log_display}")

    logger.debug(f"--- get_order_items_for_editing END for order_pk: {order_pk} ---")
    return JsonResponse({
        'success': True,
        'order_id': order.pk,
        'erp_order_id': order.erp_order_id,
        'customer_name': order.customer.customer_name,
        'formset_html': formset_html_content,
        'removed_items_log': removed_items_log_display, # Send the log
        'message': 'Items loaded for editing/removal.'
    })

@login_required
@transaction.atomic
def process_order_item_removal(request, order_pk):
    # This view remains largely the same as provided by the user.
    # It processes the RemoveOrderItemFormSet.
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)

    order = get_object_or_404(Order, pk=order_pk)

    # Permission check
    if not request.user.is_superuser and (not request.user.warehouse or order.warehouse != request.user.warehouse):
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    # Security check: Only allow removal if order is PARTIALLY_SHIPPED
    if order.status != 'PARTIALLY_SHIPPED':
        return JsonResponse({'success': False, 'message': f'Order status is "{order.get_status_display()}", cannot process removals.'}, status=400)

    # Reconstruct initial data for formset validation, including the correct balance
    # This is crucial because the form's `clean_quantity_to_remove` depends on `self.balance_quantity_to_pack`
    temp_formset_data_from_post = {} # To store POSTed order_item_id and quantity_to_remove
    for key, value in request.POST.items():
        if key.startswith('edititems-') and key.endswith('-order_item_id'):
            form_idx = key.split('-')[1]
            if form_idx not in temp_formset_data_from_post: temp_formset_data_from_post[form_idx] = {}
            temp_formset_data_from_post[form_idx]['order_item_id'] = value
        elif key.startswith('edititems-') and key.endswith('-quantity_to_remove'):
            form_idx = key.split('-')[1]
            if form_idx not in temp_formset_data_from_post: temp_formset_data_from_post[form_idx] = {}
            temp_formset_data_from_post[form_idx]['quantity_to_remove'] = value

    reconstructed_initial_for_formset = []
    for idx_str, data_dict in temp_formset_data_from_post.items():
        try:
            oi_id = int(data_dict['order_item_id'])
            oi = OrderItem.objects.get(pk=oi_id, order=order) # Ensure item belongs to this order
            total_removed_for_oi = order.get_total_removed_quantity_for_item(oi.id)
            balance = oi.quantity_ordered - oi.quantity_packed - total_removed_for_oi
            reconstructed_initial_for_formset.append({
                'order_item_id': oi_id,
                'product_name': oi.product.name, # For display if needed by form, though form makes it readonly
                'sku': oi.product.sku,
                'balance_quantity_to_pack': balance, # This is what the form needs for its max validation
                'quantity_to_remove': data_dict.get('quantity_to_remove', 0) # The value user entered
            })
        except (OrderItem.DoesNotExist, ValueError, KeyError) as e:
            logger.error(f"Error reconstructing formset initial data for validation: {e}")
            return JsonResponse({'success': False, 'message': 'Error processing form data.'}, status=400)


    # Now, create the formset with the POST data and the reconstructed initial data (for balance validation)
    # The formset factory will create forms, and each form's __init__ will use its specific initial data.
    # Note: The `initial` kwarg to formset_factory is for *extra* forms, not for existing ones.
    # We need to pass the data such that each form gets its correct `balance_quantity_to_pack`.
    # This is typically handled by passing `form_kwargs` to the formset if all forms need the same extra arg,
    # or by customizing the formset's `_construct_form` method.
    # For simplicity here, we'll validate each form individually.

    is_formset_valid = True
    cleaned_forms_data = [] # Store cleaned data from each valid form

    # Manually iterate and validate each form
    for i in range(int(request.POST.get('edititems-TOTAL_FORMS', 0))):
        form_data_for_instance = {k.replace(f'edititems-{i}-', ''): v for k, v in request.POST.items() if k.startswith(f'edititems-{i}-')}

        # Find the corresponding reconstructed initial data for this form instance
        current_form_oi_id = int(form_data_for_instance.get('order_item_id', 0))
        balance_for_this_form = 0
        for init_data in reconstructed_initial_for_formset:
            if init_data['order_item_id'] == current_form_oi_id:
                balance_for_this_form = init_data['balance_quantity_to_pack']
                break

        # Instantiate the form with POST data and the specific balance for validation
        form = RemoveOrderItemForm(
            form_data_for_instance, # This is the POST data for this form
            balance_quantity_to_pack=balance_for_this_form # Pass the balance to __init__
        )

        if form.is_valid():
            cleaned_forms_data.append(form.cleaned_data)
        else:
            is_formset_valid = False
            logger.warning(f"Form {i} errors: {form.errors.as_json()}")
            # Collect all form errors to return
            # For now, just return a generic message on first error
            return JsonResponse({'success': False, 'message': 'Invalid data in removal form. Please check quantities.'}, status=400)


    if is_formset_valid:
        removed_items_summary_for_log = order.items_removed_log or [] # Get existing log or start new
        any_actual_removal = False

        for data in cleaned_forms_data:
            order_item_id = data.get('order_item_id')
            qty_removed = data.get('quantity_to_remove', 0) # Default to 0 if not present

            if qty_removed > 0:
                try:
                    order_item = OrderItem.objects.get(pk=order_item_id, order=order)
                    # Log the removal
                    removed_items_summary_for_log.append({
                        'order_item_id': order_item.id,
                        'product_sku': order_item.product.sku,
                        'product_name': order_item.product.name,
                        'removed_qty': qty_removed,
                        'removed_at': timezone.now().isoformat() # Store timestamp of removal
                    })
                    any_actual_removal = True
                except OrderItem.DoesNotExist:
                    # This should have been caught if form_data_for_instance was correctly linked
                    messages.error(request, f"Error: Order Item ID {order_item_id} not found for this order.")
                    return JsonResponse({'success': False, 'message': f"Item ID {order_item_id} not found."}, status=400)

        if any_actual_removal:
            order.items_removed_log = removed_items_summary_for_log
            # The order.save() will trigger update_status_based_on_items_and_parcels
            # which now needs to consider items_removed_log.

        order.save() # This will call update_status_based_on_items_and_parcels
        order.refresh_from_db() # Get the potentially updated status

        messages.success(request, "Order items updated successfully. Status refreshed.")
        return JsonResponse({
            'success': True,
            'message': 'Items processed successfully! Order status may have been updated.',
            'new_order_status': order.get_status_display(), # Send back new status
            'order_id': order.pk
        })
    else:
        # This case should ideally be caught by individual form validation returning early
        logger.error(f"Order item removal formset was not valid for order {order_pk}.")
        return JsonResponse({'success': False, 'message': 'Invalid form data submitted.'}, status=400)


# --- NEW VIEWS FOR PARCEL CUSTOMS DETAILS ---
@login_required
def get_parcel_details_for_editing(request, parcel_pk):
    """
    Gets all data needed to populate the 'View/Edit Parcel' modal.
    This version is updated to use the new customers.Customer model.
    """
    # --- UPDATED: Added 'order__customer' to select_related for efficiency ---
    parcel = get_object_or_404(
        Parcel.objects.select_related(
            'order__warehouse', 'order__customer', 'packaging_type',
            'courier_company', 'customs_declaration'
        ).prefetch_related(models.Prefetch('items_in_parcel', queryset=ParcelItem.objects.select_related('order_item__product'))),
        pk=parcel_pk
    )

    if not request.user.is_superuser and (not request.user.warehouse or parcel.order.warehouse != request.user.warehouse):
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    # --- Filtering logic for declarations remains the same and is correct ---
    courier_filter = Q(courier_companies__isnull=True)
    if parcel.courier_company:
        courier_filter |= Q(courier_companies=parcel.courier_company)
    effective_env_type = None
    if parcel.packaging_type and parcel.packaging_type.environment_type in ['COLD', 'AMBIENT']:
        effective_env_type = parcel.packaging_type.environment_type
    elif parcel.order.is_cold_chain:
        effective_env_type = 'COLD'
    else:
        effective_env_type = 'AMBIENT'
    shipment_type_filter = Q()
    if effective_env_type == 'COLD':
        shipment_type_filter = Q(applies_to_cold_chain=True) | Q(applies_to_mix=True)
    elif effective_env_type == 'AMBIENT':
        shipment_type_filter = Q(applies_to_ambient=True) | Q(applies_to_mix=True)
    declarations_queryset = CustomsDeclaration.objects.filter(courier_filter & shipment_type_filter).distinct().order_by('description')
    declarations_json = list(declarations_queryset.values('pk', 'description', 'hs_code'))

    # Instantiate forms
    parcel_form = ParcelCustomsDetailForm(instance=parcel, declarations_queryset=declarations_queryset)
    item_formset = ParcelItemCustomsDetailFormSet(instance=parcel, prefix='parcelitems')

    # Prepare other data
    env_type = 'COLD' if parcel.order.is_cold_chain else 'AMBIENT'
    available_packaging = list(PackagingType.objects.filter(environment_type=env_type, is_active=True).values('id', 'name', 'default_length_cm', 'default_width_cm', 'default_height_cm'))

    # --- THE FIX: Get customer info from the new 'order.customer' relationship ---
    parcel_data = {
        'parcel_code_system': parcel.parcel_code_system,
        'courier_name': parcel.courier_company.name if parcel.courier_company else "N/A",
        'tracking_number': parcel.tracking_number,
        'status_display': parcel.get_status_display(),
        'packaging_type_display': parcel.get_packaging_type_display(),
        'created_at': parcel.created_at.strftime('%Y-%m-%d %H:%M') if parcel.created_at else "N/A",
        'shipped_at': parcel.shipped_at.strftime('%Y-%m-%d %H:%M') if parcel.shipped_at else "N/A",

        # Correctly access customer data
        'customer_name': parcel.order.customer.customer_name if parcel.order.customer else "N/A",
        'company_name': parcel.order.customer.company_name if parcel.order.customer else "",
        'recipient_address_line1': parcel.order.customer.address_line1 if parcel.order.customer else "",
        'recipient_address_city': parcel.order.customer.city if parcel.order.customer else "",
        'recipient_address_state': parcel.order.customer.state if parcel.order.customer else "",
        'recipient_address_zip': parcel.order.customer.zip_code if parcel.order.customer else "",
        'recipient_address_country': parcel.order.customer.country if parcel.order.customer else "",
        'recipient_phone': parcel.order.customer.phone_number if parcel.order.customer else "",
    }

    # Prepare context for rendering the template
    context = {
        'parcel': parcel,
        'parcel_form': parcel_form,
        'item_formset': item_formset,
        'parcel_data': parcel_data,
        'available_packaging': available_packaging,
        'declarations_for_template': declarations_queryset,
    }
    form_html = render_to_string('operation/partials/_parcel_edit_form_content.html', context, request=request)

    # Return all data as a JSON response
    return JsonResponse({
        'success': True,
        'form_html': form_html,
        'parcel_data': parcel_data,
        'available_packaging': available_packaging,
        'declarations_json': declarations_json,
    })





@login_required
@transaction.atomic
def update_parcel_customs_details(request, parcel_pk):
    """
    Handles the AJAX POST request from the parcel edit modal to save customs details.
    This view now includes the corrected filtering logic for form validation.
    """
    if request.method != 'POST':
        logger.warning(f"update_parcel_customs_details received non-POST request for parcel {parcel_pk}")
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)

    parcel = get_object_or_404(
        Parcel.objects.select_related('order__warehouse', 'courier_company', 'packaging_type'),
        pk=parcel_pk
    )
    logger.info(f"Updating customs details for Parcel PK: {parcel_pk}, System Code: {parcel.parcel_code_system}")

    # Permission Check
    if not request.user.is_superuser and (not request.user.warehouse or parcel.order.warehouse != request.user.warehouse):
        logger.warning(f"User {request.user.email} permission denied for parcel {parcel_pk}.")
        return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)

    # --- START: Corrected Filtering Logic ---
    # This block must be here to ensure the form can validate the submitted data.
    # It mirrors the logic used to display the options in the first place.
    courier_filter = Q(courier_companies__isnull=True)
    if parcel.courier_company:
        courier_filter |= Q(courier_companies=parcel.courier_company)

    # Step 1: Determine the single, effective environment type for the parcel.
    effective_env_type = None
    if parcel.packaging_type and parcel.packaging_type.environment_type in ['COLD', 'AMBIENT']:
        effective_env_type = parcel.packaging_type.environment_type
    elif parcel.order.is_cold_chain:
        effective_env_type = 'COLD'
    else:
        effective_env_type = 'AMBIENT'

    # Step 2: Build the filter based on the determined type.
    shipment_type_filter = Q()
    if effective_env_type == 'COLD':
        shipment_type_filter = Q(applies_to_cold_chain=True) | Q(applies_to_mix=True)
    elif effective_env_type == 'AMBIENT':
        shipment_type_filter = Q(applies_to_ambient=True) | Q(applies_to_mix=True)

    valid_declarations_qs = CustomsDeclaration.objects.filter(
        courier_filter & shipment_type_filter
    ).distinct()

    # --- END: Corrected Filtering Logic ---


    # Pass the filtered queryset to the form for proper validation
    parcel_form = ParcelCustomsDetailForm(request.POST, instance=parcel, declarations_queryset=valid_declarations_qs)
    item_formset = ParcelItemCustomsDetailFormSet(request.POST, instance=parcel, prefix='parcelitems')

    if parcel_form.is_valid() and item_formset.is_valid():
        logger.info(f"Forms are valid for parcel {parcel_pk}.")

        # Your existing logic for saving the data is correct.
        updated_parcel = parcel_form.save(commit=False)

        length = parcel_form.cleaned_data.get('length')
        width = parcel_form.cleaned_data.get('width')
        height = parcel_form.cleaned_data.get('height')

        if length and width and height:
            try:
                updated_parcel.dimensional_weight_kg = (Decimal(str(length)) * Decimal(str(width)) * Decimal(str(height))) / Decimal('5000')
            except Exception as e:
                logger.error(f"Error calculating dimensional weight for parcel {parcel_pk}: {e}")
                updated_parcel.dimensional_weight_kg = None
        else:
            updated_parcel.dimensional_weight_kg = None

        updated_parcel.save()
        item_formset.save()

        logger.info(f"Successfully updated customs details for parcel {parcel_pk}.")
        messages.success(request, f"Customs details for Parcel {parcel.parcel_code_system} updated successfully.")
        return JsonResponse({'success': True, 'message': 'Customs details updated successfully.'})
    else:
        # Your existing error handling logic is correct.
        errors = {}
        if parcel_form.errors:
            errors['parcel_form'] = parcel_form.errors.as_json()
        if item_formset.errors:
            formset_errors_list = []
            for form_errors in item_formset.errors:
                if form_errors:
                    formset_errors_list.append(form_errors.as_json())
            if formset_errors_list:
                errors['item_formset'] = formset_errors_list
        if item_formset.non_form_errors():
            errors['item_formset_non_form'] = item_formset.non_form_errors()

        return JsonResponse({'success': False, 'message': "Validation failed. Please check the form details.", 'errors': errors}, status=400)

@login_required
def manage_customs_declarations(request):
    if not (request.user.is_superuser or request.user.warehouse):
        messages.error(request, "You do not have permission to access this page.")
        return redirect('inventory:inventory_batch_list_view')

    selected_courier_id = request.GET.get('courier_company')
    selected_shipment_type = request.GET.get('shipment_type')

    declarations_qs = CustomsDeclaration.objects.prefetch_related('courier_companies').all()

    # Updated courier filtering logic
    if selected_courier_id:
        if selected_courier_id == "generic":
            declarations_qs = declarations_qs.filter(courier_companies__isnull=True)
        else:
            # Show declarations for the specific courier OR generic ones
            declarations_qs = declarations_qs.filter(Q(courier_companies__id=selected_courier_id) | Q(courier_companies__isnull=True))

    # Updated shipment type filtering logic
    if selected_shipment_type:
        if selected_shipment_type == 'AMBIENT':
            declarations_qs = declarations_qs.filter(applies_to_ambient=True)
        elif selected_shipment_type == 'COLD_CHAIN':
            declarations_qs = declarations_qs.filter(applies_to_cold_chain=True)
        elif selected_shipment_type == 'MIX':
            declarations_qs = declarations_qs.filter(applies_to_mix=True)

    declarations = declarations_qs.order_by('description', 'hs_code')
    couriers = CourierCompany.objects.all().order_by('name')
    # Manually define choices for the template filter dropdown
    shipment_type_choices = [('AMBIENT', 'Ambient Only'), ('COLD_CHAIN', 'Cold Chain Only'), ('MIX', 'Mixed')]


    if request.method == 'POST':
        form = CustomsDeclarationForm(request.POST)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Customs declaration added successfully.")
                return redirect('operation:manage_customs_declarations')
            except IntegrityError:
                messages.error(request, "Failed to add declaration. A declaration with this description and HS code may already exist.")
        else:
            messages.error(request, "Please correct the errors below when adding a new declaration.")
    else:
        form = CustomsDeclarationForm()

    context = {
        'form': form,
        'declarations': declarations,
        'couriers': couriers,
        'shipment_type_choices': shipment_type_choices,
        'selected_courier_id': selected_courier_id,
        'selected_shipment_type': selected_shipment_type,
        'page_title': "Manage Customs Declarations",
        'user': request.user
    }
    return render(request, 'operation/manage_customs_declarations.html', context)


@login_required
def edit_customs_declaration(request, pk):
    if not (request.user.is_superuser or request.user.warehouse):
        messages.error(request, "You do not have permission to perform this action.")
        return redirect('operation:manage_customs_declarations')

    declaration = get_object_or_404(CustomsDeclaration, pk=pk)

    if request.method == 'POST':
        form = CustomsDeclarationForm(request.POST, instance=declaration)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, f"Declaration '{declaration.description[:30]}...' updated successfully.")
            except IntegrityError as e:
                if 'unique_decl_with_courier_desc_hs_type' in str(e) or \
                   'unique_decl_without_courier_desc_hs_type' in str(e):
                    messages.error(request, "Update failed. This combination of Description, HS Code, Courier, and Shipment Type already exists for another entry.")
                else:
                    messages.error(request, "Update failed due to a data conflict or integrity issue.")
            return redirect('operation:manage_customs_declarations')
        else:
            error_list = []
            for field_errors in form.errors.values():
                error_list.extend(field_errors)
            messages.error(request, "Could not update declaration: " + "; ".join(error_list))
    # If GET or form invalid, redirect back to the list.
    # The modal opening logic should ideally be handled by JS if errors occur to repopulate the modal.
    # For simplicity, this redirects to the list page, losing modal state on error.
    return redirect('operation:manage_customs_declarations')


# delete_customs_declaration view remains the same as provided in the previous step.
@login_required
@require_POST
def delete_customs_declaration(request, pk):
    if not (request.user.is_superuser or request.user.warehouse):
        messages.error(request, "You do not have permission to perform this action.")
        return redirect('operation:manage_customs_declarations')

    declaration = get_object_or_404(CustomsDeclaration, pk=pk)
    try:
        desc_preview = declaration.description[:30]
        declaration.delete()
        messages.success(request, f"Declaration '{desc_preview}...' deleted successfully.")
    except Exception as e:
        messages.error(request, f"Error deleting declaration: {str(e)}")
    return redirect('operation:manage_customs_declarations')


@login_required
def packaging_management(request):
    """
    Manages the display and creation of PackagingTypes and PackagingMaterials.
    The form for adding a new packaging type is simplified by removing dimension fields.
    """
    packaging_type_form = PackagingTypeForm()
    packaging_material_form = PackagingMaterialForm()

    if request.method == 'POST':
        if 'submit_packaging_type' in request.POST:
            form_with_all_fields = PackagingTypeForm(request.POST)
            if form_with_all_fields.is_valid():
                form_with_all_fields.save()
                messages.success(request, 'New packaging type added successfully.')
                return redirect('operation:packaging_management')
            else:
                # If form is invalid, pass the form with errors back
                # but still remove the dimension fields for a consistent display
                packaging_type_form = form_with_all_fields
                messages.error(request, 'Error adding packaging type. Please check the form below.')

        elif 'submit_packaging_material' in request.POST:
            form = PackagingMaterialForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, 'New packaging material added successfully.')
                return redirect('operation:packaging_management')
            else:
                packaging_material_form = form
                messages.error(request, 'Error adding packaging material.')

    # For both GET requests and POST requests with errors,
    # remove dimension fields from the "Add" form.
    if 'default_length_cm' in packaging_type_form.fields:
        del packaging_type_form.fields['default_length_cm']
    if 'default_width_cm' in packaging_type_form.fields:
        del packaging_type_form.fields['default_width_cm']
    if 'default_height_cm' in packaging_type_form.fields:
        del packaging_type_form.fields['default_height_cm']

    # Querysets for display and data preparation
    packaging_types_qs = PackagingType.objects.prefetch_related(
        'packagingtypematerialcomponent_set__packaging_material'
    ).order_by('name')
    all_materials_qs = PackagingMaterial.objects.order_by('name')

    # Prepare data for JavaScript-powered Edit Modal
    all_materials_json = json.dumps(list(all_materials_qs.values('pk', 'name')), cls=DjangoJSONEncoder)

    packaging_types_data = []
    for pt in packaging_types_qs:
        components = pt.packagingtypematerialcomponent_set.all()
        component_map = {comp.packaging_material.id: comp.quantity for comp in components}

        packaging_type_dict = {
            'pk': pt.pk,
            'name': pt.name,
            'type_code': pt.type_code or "",
            'environment_type': pt.environment_type,
            'is_active': pt.is_active,
            'default_length_cm': pt.default_length_cm,
            'default_width_cm': pt.default_width_cm,
            'default_height_cm': pt.default_height_cm,
        }

        packaging_types_data.append({
            'instance': pt,
            'components': components,
            'packaging_type_json': json.dumps(packaging_type_dict, cls=DjangoJSONEncoder),
            'component_map_json': json.dumps(component_map, cls=DjangoJSONEncoder)
        })

    context = {
        'page_title': 'Packaging Management',
        'packaging_type_form': packaging_type_form,
        'packaging_material_form': packaging_material_form,
        'packaging_types_data': packaging_types_data,
        'packaging_materials': all_materials_qs,
        'all_materials_json': all_materials_json,
    }
    return render(request, 'operation/packaging_management.html', context)

@login_required
def load_edit_packaging_type_form(request, pk):
    """
    This view is called by HTMX to load the edit form into a modal.
    It now pre-processes the materials list to remove the need for custom template filters.
    """
    packaging_type = get_object_or_404(PackagingType, pk=pk)
    form = PackagingTypeForm(instance=packaging_type)
    all_materials = PackagingMaterial.objects.all()

    # Create a map of existing components for easy lookup
    component_map = {comp.packaging_material.id: comp.quantity for comp in packaging_type.packagingtypematerialcomponent_set.all()}

    # Build a list of dictionaries with all data the template needs
    materials_data = []
    for material in all_materials:
        materials_data.append({
            'id': material.id,
            'name': material.name,
            'quantity': component_map.get(material.id, '')  # Get current quantity or an empty string
        })

    context = {
        'form': form,
        'packaging_type': packaging_type,
        'materials_data': materials_data, # Pass this new, pre-processed list
    }
    return render(request, 'operation/partials/_edit_packaging_type_form.html', context)

@login_required
def edit_packaging_type(request, pk):
    """
    Handles the POST submission for updating a PackagingType from the modal form.
    """
    packaging_type = get_object_or_404(PackagingType, pk=pk)

    if request.method == 'POST':
        form = PackagingTypeForm(request.POST, instance=packaging_type)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # Save the main form data (name, code, etc.)
                    updated_packaging_type = form.save()

                    # Keep track of materials that have a quantity in the form
                    processed_material_ids = set()

                    # Iterate through all POST items to find material quantities
                    for key, value in request.POST.items():
                        if key.startswith('quantity_') and value.strip():
                            try:
                                material_id = int(key.split('_')[1])
                                quantity = int(value)

                                if quantity > 0:
                                    # This material should be linked to the packaging type
                                    material = get_object_or_404(PackagingMaterial, id=material_id)

                                    # Use update_or_create for efficiency. It will either update an
                                    # existing component or create a new one.
                                    PackagingTypeMaterialComponent.objects.update_or_create(
                                        packaging_type=updated_packaging_type,
                                        packaging_material=material,
                                        defaults={'quantity': quantity}
                                    )
                                    processed_material_ids.add(material_id)

                            except (ValueError, IndexError, PackagingMaterial.DoesNotExist):
                                # Ignore if the key is malformed or material doesn't exist
                                continue

                    # Remove any components that were not submitted in the form
                    # (i.e., their quantity was cleared or set to 0)
                    PackagingTypeMaterialComponent.objects.filter(
                        packaging_type=updated_packaging_type
                    ).exclude(
                        packaging_material_id__in=processed_material_ids
                    ).delete()

                messages.success(request, f"Packaging Type '{updated_packaging_type.name}' updated successfully.")

            except Exception as e:
                messages.error(request, f"An error occurred while saving: {e}")

            return redirect('operation:packaging_management')
        else:
            # If the form has validation errors (e.g., name is blank)
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"Error in '{field}': {error}")
            return redirect('operation:packaging_management')

    # If someone tries to access this URL with a GET request, just redirect them
    # as the form is now loaded into the modal by a different view.
    return redirect('operation:packaging_management')




@login_required
def get_customer_shipment_history(request, customer_pk):
    """
    Fetches all parcels for a specific customer to display in a modal.
    """
    customer = get_object_or_404(Customer, pk=customer_pk)

    # Get all parcels for this customer, across all their orders
    parcels = Parcel.objects.filter(order__customer=customer).select_related(
        'order', 'courier_company'
    ).order_by('-created_at')

    context = {
        'customer': customer,
        'parcels': parcels,
    }
    return render(request, 'operation/partials/_customer_shipments_modal_content.html', context)


@login_required
def get_airway_bill_details(request, parcel_pk):
    """
    Fetches all necessary data to populate the Air Waybill modal.
    """
    parcel = get_object_or_404(
        Parcel.objects.select_related(
            'order__customer',
            'order__warehouse',
            'courier_company',
            'customs_declaration'
        ),
        pk=parcel_pk
    )

    context = {
        'parcel': parcel,
        'form': AirwayBillForm(instance=parcel) # Pass an instance of the new form
    }
    return render(request, 'operation/partials/_airway_bill_modal_content.html', context)

@require_POST # Ensures this view only accepts POST requests
@login_required
def save_airway_bill(request, parcel_pk):
    """
    Saves the Tracking ID and Estimated Cost from the Air Waybill modal.
    Includes a resilient call to the background tracking task.
    """
    parcel = get_object_or_404(Parcel, pk=parcel_pk)
    form = AirwayBillForm(request.POST, instance=parcel)

    if form.is_valid():
        updated_parcel = form.save()

        # Check if we need to update status and trigger tracking
        if updated_parcel.tracking_number and updated_parcel.status == 'READY_TO_SHIP':
            updated_parcel.status = 'READY_TO_SHIP'
            updated_parcel.shipped_at = timezone.now()
            updated_parcel.save(update_fields=['status', 'shipped_at'])

        return JsonResponse({'success': True, 'message': 'Air Waybill details saved successfully.'})
    else:
        error_message = ". ".join([f"{field}: {error[0]}" for field, error in form.errors.items()])
        return JsonResponse({'success': False, 'message': error_message or "Invalid data submitted."}, status=400)



@login_required
def get_parcel_tracking_history(request, parcel_pk):
    """
    Fetches the full tracking log for a parcel to display in a modal.
    """
    parcel = get_object_or_404(
        Parcel.objects.prefetch_related('tracking_logs'),
        pk=parcel_pk
    )
    # The tracking logs are already ordered by timestamp due to the model's Meta ordering
    return render(request, 'operation/partials/_parcel_tracking_history_modal.html', {'parcel': parcel})

# @login_required
# def manual_trigger_tracking_update(request, parcel_pk):
#     """
#     A simple view to manually trigger the tracking update task for a given parcel.
#     This is for testing and debugging purposes.
#     """
#     from .tasks import update_parcel_tracking_status

#     # We call the function directly, bypassing the Celery .delay() method.
#     result_message = update_parcel_tracking_status(parcel_pk)

#     return HttpResponse(f"Tracking update task executed for Parcel ID {parcel_pk}.<br>Result: {result_message}<br><br><a href='/operation/list/?tab=parcels_details'>Go back to Parcels List</a>")

@require_POST
@login_required
def trace_selected_parcels(request):
    """
    Handles the 'Trace Selected' button click for bulk manual tracking updates.
    Loops through selected parcels and calls the tracking service for each one.
    """
    try:
        data = json.loads(request.body)
        parcel_ids = data.get('parcel_ids', [])
        logger.debug(f"[TraceParcels] Received request to trace parcel IDs: {parcel_ids}")

        if not parcel_ids:
            return JsonResponse({'success': False, 'message': 'No parcels selected.'}, status=400)

        parcels_to_trace = Parcel.objects.filter(id__in=parcel_ids)
        success_count = 0
        error_count = 0
        status_updates = []

        for parcel in parcels_to_trace:
            try:
                # Call the centralized service function for each parcel
                success, message = update_parcel_tracking_from_api(parcel)
                if success:
                    success_count += 1
                    # Optional: Log the specific message for each parcel
                    logger.info(f"Tracking update for Parcel {parcel.id}: {message}")
                    if "Status updated" in message:
                        status_updates.append(parcel.parcel_code_system)
                else:
                    error_count += 1
                    logger.warning(f"Tracking update failed for Parcel {parcel.id}: {message}")

            except Exception as e:
                # Catch any unexpected errors during the service call for a single parcel
                logger.error(f"[TraceParcels] Unexpected error processing parcel {parcel.id}: {e}", exc_info=True)
                error_count += 1

        # --- Construct a final summary message for the user ---
        final_message = f"Tracking update complete. Success: {success_count}, Failed: {error_count}."
        if status_updates:
            final_message += f" Status changed for: {', '.join(status_updates)}."

        return JsonResponse({'success': True, 'message': final_message})

    except json.JSONDecodeError:
        logger.error("[TraceParcels] Invalid JSON in request body.")
        return JsonResponse({'success': False, 'message': 'Invalid JSON in request body.'}, status=400)
    except Exception as e:
        logger.error(f"[TraceParcels] Critical error in view: {e}", exc_info=True)
        return JsonResponse({'success': False, 'message': 'A critical server error occurred.'}, status=500)

@login_required
def print_selected_parcels(request):
    parcel_ids_str = request.GET.get('ids', '')
    if not parcel_ids_str:
        return HttpResponse("No parcel IDs provided.", status=400)

    parcel_ids = [int(id) for id in parcel_ids_str.split(',') if id.isdigit()]

    parcels = Parcel.objects.filter(id__in=parcel_ids).select_related(
        'order__customer', 'courier_company', 'packaging_type'
    ).prefetch_related(
        'items_in_parcel__order_item__product'
    )

    if not parcels:
        return HttpResponse("No valid parcels found for the given IDs.", status=404)

    # Summary Data Calculation
    total_parcels = parcels.count()
    cold_chain_count = parcels.filter(order__is_cold_chain=True).count()
    ambient_count = total_parcels - cold_chain_count

    courier_counts = parcels.values('courier_company__name').annotate(count=Count('courier_company__name')).order_by('-count')

    product_quantities = {}
    for parcel in parcels:
        for item in parcel.items_in_parcel.all():
            product_name = item.order_item.product.name
            quantity = item.quantity_shipped_in_this_parcel
            if product_name in product_quantities:
                product_quantities[product_name] += quantity
            else:
                product_quantities[product_name] = quantity

    sorted_product_quantities = sorted(product_quantities.items(), key=lambda x: x[1], reverse=True)


    context = {
        'parcels': parcels,
        'summary': {
            'total_parcels': total_parcels,
            'cold_chain_count': cold_chain_count,
            'ambient_count': ambient_count,
            'courier_counts': courier_counts,
            'product_quantities': sorted_product_quantities,
        }
    }
    return render(request, 'operation/printable_parcels.html', context)



# 3. Edit Invoice (for payment date)
@login_required
def edit_courier_invoice(request, invoice_id):
    invoice = get_object_or_404(CourierInvoice, pk=invoice_id)
    if request.method == 'POST':
        payment_date = request.POST.get('payment_date')
        if payment_date:
            invoice.payment_date = payment_date
            invoice.save()
            messages.success(request, 'Payment date updated.')
    return redirect('operation:courier_invoice_list')


# 4. Display Billed Parcels
@login_required
def billed_parcels_list(request):
    billed_items = CourierInvoiceItem.objects.select_related('courier_invoice', 'parcel').all()
    return render(request, 'operation/billed_parcels_list.html', {'billed_items': billed_items})


# 6. Cost Comparison Report
@login_required
def cost_comparison_report(request):
    parcels_with_costs = Parcel.objects.filter(
        estimated_cost__isnull=False,
        actual_shipping_cost__isnull=False
    ).annotate(
        cost_difference=F('actual_shipping_cost') - F('estimated_cost')
    )
    return render(request, 'operation/cost_comparison_report.html', {'parcels': parcels_with_costs})


# 7. Generate Client Invoice
@login_required
def generate_client_invoice(request, parcel_id):
    parcel = get_object_or_404(Parcel.objects.select_related('order__customer'), pk=parcel_id)
    # This view would gather all necessary details and render them into an invoice template.
    # For PDF generation, you could integrate libraries like WeasyPrint or ReportLab.
    return render(request, 'operation/client_invoice_template.html', {'parcel': parcel})


@login_required
def courier_invoice_list(request):
    """
    Handles both displaying the list of invoices and uploading a new one.
    Now separates success toasts from error messages.
    """
    if request.method == 'POST':
        form = CourierInvoiceForm(request.POST, request.FILES)
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.uploaded_by = request.user
            invoice.save()

            # The view now expects four return values from the parsing function
            created_count, errors, updated_count, successes = parse_invoice_file(invoice)

            # Display success messages as green toasts
            for msg in successes:
                messages.success(request, msg)

            # Display informational and error messages
            has_critical_error = False
            if errors:
                for error in errors:
                    if "Info:" in error:
                        messages.info(request, error)
                    else:
                        messages.error(request, error)
                        if "Critical" in error:
                            has_critical_error = True

            # This provides a final summary of items created/updated
            if not errors or not has_critical_error:
                summary_parts = []
                if created_count > 0:
                    summary_parts.append(f"Created {created_count} new item(s)")
                if updated_count > 0:
                    summary_parts.append(f"Updated {updated_count} existing item(s)")

                if summary_parts:
                    item_summary = ", ".join(summary_parts) + "."
                    messages.success(request, f"Processing complete. {item_summary}")

            return redirect('operation:courier_invoice_list')
        else:
             for field, field_errors in form.errors.items():
                 for error in field_errors:
                     messages.error(request, f"{field.capitalize()}: {error}")
             return redirect('operation:courier_invoice_list')

    invoices = CourierInvoice.objects.select_related('courier_company').prefetch_related('items').all().order_by('-invoice_date')
    form = CourierInvoiceForm()
    return render(request, 'operation/courier_invoice_list.html', {'invoices': invoices, 'form': form})


@login_required
def invoice_item_report(request):
    """
    Handles the initial page load and filter submissions for the Invoice Item Report.
    FIX: Added date range filtering for shipment date.
    """
    is_ajax_filter = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    sort_by = request.GET.get('sort_by', '-courier_invoice__invoice_date')
    allowed_sort_fields = [
            'courier_invoice__invoice_date', '-courier_invoice__invoice_date', 'actual_cost',
            '-actual_cost', 'tracking_number', '-tracking_number', 'parcel__estimated_cost',
            '-parcel__estimated_cost', 'receiver_state', '-receiver_state', 'destination_name',
            '-destination_name', 'parcel__order__customer__customer_name',
            '-parcel__order__customer__customer_name', 'cost_gap', '-cost_gap'
        ]
    if sort_by not in allowed_sort_fields:
        sort_by = '-courier_invoice__invoice_date'

    items_query = CourierInvoiceItem.objects.select_related(
        'parcel__order__customer',
        'courier_invoice__courier_company'
    ).annotate(
        cost_gap=Case(
            When(parcel__estimated_cost__isnull=False, then=F('actual_cost') - F('parcel__estimated_cost')),
            default=Value(None)
        )
    ).order_by(sort_by)

    # --- Filter Logic ---
    selected_dispute_status = request.GET.get('dispute_status', '')

    if selected_dispute_status == 'pending':
        # FIX: Changed 'items' to 'items_query'
        items_query = items_query.filter(dispute_date__isnull=False, final_amount_date__isnull=True)
    elif selected_dispute_status == 'finalized':
        # FIX: Changed 'items' to 'items_query'
        items_query = items_query.filter(final_amount_date__isnull=False)

    courier_id = request.GET.get('courier')
    if courier_id:
        items_query = items_query.filter(courier_invoice__courier_company_id=courier_id)

    query = request.GET.get('q')
    if query:
        items_query = items_query.filter(
            Q(tracking_number__icontains=query) |
            Q(courier_invoice__invoice_number__icontains=query)
        ).distinct()

    # FIX: Add date range filtering logic
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            items_query = items_query.filter(parcel__created_at__gte=start_date)
        except ValueError:
            pass # Ignore invalid date format

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            end_of_day = datetime.combine(end_date, datetime.time.max)
            items_query = items_query.filter(parcel__created_at__lte=end_of_day)
        except ValueError:
            pass # Ignore invalid date format

    # --- Pagination and Context ---
    paginator = Paginator(items_query, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    for item in page_obj:
        # ... (cost history processing remains the same) ...
        cumulative_costs = []
        running_total = Decimal('0.0')
        if isinstance(item.cost_history, list):
            sorted_history = sorted(item.cost_history, key=lambda x: x.get('date', ''))
            for charge in sorted_history:
                running_total += Decimal(str(charge.get('cost', 0)))
                cumulative_costs.append(running_total)
        item.display_costs = cumulative_costs

    couriers = CourierCompany.objects.filter(is_active=True).order_by('name')

    context = {
        'items': page_obj,
        'couriers': couriers,
        'selected_courier': int(courier_id) if courier_id else None,
        'query': query,
        'start_date': start_date_str, # Pass date strings back to template
        'end_date': end_date_str,   # Pass date strings back to template
        'current_sort': sort_by,
        'total_items_count': paginator.count,
        'selected_dispute_status': selected_dispute_status,
    }

    if is_ajax_filter:
        return render(request, 'operation/partials/_invoice_item_report_table_with_pagination.html', context)

    return render(request, 'operation/invoice_item_report.html', context)

@login_required
def load_more_invoice_items(request):
    """
    Handles AJAX requests for the 'Explore More' button.
    FIX: Added date range filtering to match the main view.
    """
    sort_by = request.GET.get('sort_by', '-courier_invoice__invoice_date')
    allowed_sort_fields = [
        'courier_invoice__invoice_date', '-courier_invoice__invoice_date', 'actual_cost',
        '-actual_cost', 'tracking_number', '-tracking_number', 'parcel__estimated_cost',
        '-parcel__estimated_cost', 'receiver_state', '-receiver_state', 'destination_name',
        '-destination_name', 'parcel__order__customer__customer_name',
        '-parcel__order__customer__customer_name', 'cost_gap', '-cost_gap'
    ]

    items_query = CourierInvoiceItem.objects.select_related(
        'parcel__order__customer', 'courier_invoice__courier_company'
    ).annotate(
        cost_gap=Case(
            When(parcel__estimated_cost__isnull=False, then=F('actual_cost') - F('parcel__estimated_cost')),
            default=Value(None)
        )
    ).order_by(sort_by)

    courier_id = request.GET.get('courier')
    if courier_id:
        items_query = items_query.filter(courier_invoice__courier_company_id=courier_id)

    query = request.GET.get('q')
    if query:
        items_query = items_query.filter(
            Q(tracking_number__icontains=query) | Q(courier_invoice__invoice_number__icontains=query)
        ).distinct()

    # FIX: Add date range filtering logic here as well
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            items_query = items_query.filter(parcel__created_at__gte=start_date)
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            end_of_day = datetime.combine(end_date, datetime.time.max)
            items_query = items_query.filter(parcel__created_at__lte=end_of_day)
        except ValueError:
            pass

    paginator = Paginator(items_query, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # ... (cost history processing is the same) ...
    for item in page_obj:
        cumulative_costs = []
        running_total = Decimal('0.0')
        if isinstance(item.cost_history, list):
            sorted_history = sorted(item.cost_history, key=lambda x: x.get('date', ''))
            for charge in sorted_history:
                running_total += Decimal(str(charge.get('cost', 0)))
                cumulative_costs.append(running_total)
        item.display_costs = cumulative_costs


    context = {'items': page_obj.object_list}
    html_rows = render_to_string('operation/partials/_invoice_item_report_rows_only.html', context)

    return JsonResponse({'html': html_rows, 'has_next': page_obj.has_next()})

@login_required
def view_parcel_items(request, item_id):
    """
    Handles the AJAX request to fetch and display the contents of a specific parcel
    linked to a courier invoice item.
    """
    # Fetch the specific invoice item, which links to the parcel
    invoice_item = get_object_or_404(
        CourierInvoiceItem.objects.select_related(
            'parcel__order__customer', # Pre-fetch related data for efficiency
            'parcel__packaging_type'  # <-- Added this line
        ).prefetch_related(
            # Pre-fetch all items within the parcel, including product and batch details
            'parcel__items_in_parcel__order_item__product',
            'parcel__items_in_parcel__shipped_from_batch'
        ),
        pk=item_id
    )

    parcel = invoice_item.parcel

    # Render just the modal content partial
    return render(request, 'operation/partials/_view_parcel_items_modal_content.html', {'parcel': parcel})


@login_required
def get_dispute_details(request, item_id):
    """
    Handles the AJAX request to fetch and render the dispute form for the modal.
    """
    invoice_item = get_object_or_404(CourierInvoiceItem, pk=item_id)
    form = DisputeForm(instance=invoice_item)
    # This is now for a single new entry
    new_update_form = DisputeUpdateForm(prefix='new_update')

    context = {
        'item': invoice_item,
        'form': form,
        'new_update_form': new_update_form,
    }
    return render(request, 'operation/partials/_dispute_modal_content.html', context)

@login_required
@require_POST
def save_dispute_details(request, item_id):
    """
    Handles the POST request to save dispute details from the modal form.
    """
    invoice_item = get_object_or_404(CourierInvoiceItem, pk=item_id)
    form = DisputeForm(request.POST, instance=invoice_item)
    new_update_form = DisputeUpdateForm(request.POST, prefix='new_update')

    if form.is_valid():
        dispute_item = form.save()

        # Check if a new update was submitted
        if new_update_form.is_valid() and new_update_form.cleaned_data.get('update_date'):
            # Get existing history, or start a new list
            history = dispute_item.dispute_history or []

            # Append the new entry
            history.append({
                'update_date': new_update_form.cleaned_data['update_date'].strftime('%Y-%m-%d'),
                'remarks': new_update_form.cleaned_data.get('remarks', '')
            })

            # Save the updated history list
            dispute_item.dispute_history = sorted(history, key=lambda x: x['update_date'])
            dispute_item.save(update_fields=['dispute_history'])

        return JsonResponse({'success': True, 'message': 'Dispute details saved successfully.'})
    else:
        errors = form.errors.as_json()
        logger.error(f"Dispute form save failed for item {item_id}: {errors}")
        return JsonResponse({'success': False, 'message': 'Please correct the errors.', 'errors': errors}, status=400)

# @login_required
# def export_parcels_to_excel(request):
#     """
#     Generates and serves an Excel file of selected parcels with calculated shipment charges.
#     This view is for superusers only.
#     """
#     if not request.user.is_superuser:
#         return HttpResponseForbidden("You do not have permission to access this resource.")

#     # 1. Get data from query parameters
#     parcel_ids_str = request.GET.get('ids', '')
#     margin_str = request.GET.get('margin', '0')
#     rate_str = request.GET.get('rate', '1')

#     if not parcel_ids_str:
#         return HttpResponse("No parcel IDs provided.", status=400)

#     # 2. Validate and process input data
#     try:
#         parcel_ids = [int(id) for id in parcel_ids_str.split(',')]
#         margin_multiplier = 1 + (Decimal(margin_str) / Decimal('100'))
#         exchange_rate = Decimal(rate_str)
#         if exchange_rate == 0:
#             return HttpResponse("Exchange rate cannot be zero.", status=400)
#     except (ValueError, TypeError):
#         return HttpResponse("Invalid margin or exchange rate provided.", status=400)

#     # 3. Fetch data from the database
#     parcels = Parcel.objects.filter(id__in=parcel_ids).select_related(
#         'order__warehouse',
#         'packaging_type',
#         'billing_item'
#     ).order_by('created_at')

#     # 4. Create the Excel workbook in memory
#     workbook = openpyxl.Workbook()
#     sheet = workbook.active
#     sheet.title = "Parcels Export"

#     # Write headers
#     headers = [
#         "Order#",
#         "Tracking Number",
#         "Shipment Date",
#         "Warehouse",
#         "Type",
#         "Shipment cost"
#     ]
#     sheet.append(headers)

#     # 5. Write data rows
#     for parcel in parcels:
#         # Calculate shipment charges
#         shipment_charges = Decimal('0.00')
#         if parcel.billing_item and parcel.billing_item.actual_cost is not None:
#             billing_cost = parcel.billing_item.actual_cost
#             shipment_charges = (billing_cost / exchange_rate) * margin_multiplier

#         # Prepare row data
#         row_data = [
#             parcel.order.erp_order_id,
#             parcel.tracking_number,
#             parcel.created_at.strftime('%Y-%m-%d'),
#             parcel.order.warehouse.name if parcel.order and parcel.order.warehouse else 'N/A',
#             parcel.packaging_type.name if parcel.packaging_type else 'N/A',
#             round(shipment_charges, 2)
#         ]
#         sheet.append(row_data)

#     # 6. Prepare the HTTP response
#     response = HttpResponse(
#         content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
#     )
#     response['Content-Disposition'] = 'attachment; filename="parcels_export.xlsx"'
#     workbook.save(response)

#     return response


STATE_MAP = {
    'AL': 'ALABAMA', 'AK': 'ALASKA', 'AZ': 'ARIZONA', 'AR': 'ARKANSAS', 'CA': 'CALIFORNIA',
    'CO': 'COLORADO', 'CT': 'CONNECTICUT', 'DE': 'DELAWARE', 'FL': 'FLORIDA', 'GA': 'GEORGIA',
    'HI': 'HAWAII', 'ID': 'IDAHO', 'IL': 'ILLINOIS', 'IN': 'INDIANA', 'IA': 'IOWA',
    'KS': 'KANSAS', 'KY': 'KENTUCKY', 'LA': 'LOUISIANA', 'ME': 'MAINE', 'MD': 'MARYLAND',
    'MA': 'MASSACHUSETTS', 'MI': 'MICHIGAN', 'MN': 'MINNESOTA', 'MS': 'MISSISSIPPI',
    'MO': 'MISSOURI', 'MT': 'MONTANA', 'NE': 'NEBRASKA', 'NV': 'NEVADA', 'NH': 'NEW HAMPSHIRE',
    'NJ': 'NEW JERSEY', 'NM': 'NEW MEXICO', 'NY': 'NEW YORK', 'NC': 'NORTH CAROLINA',
    'ND': 'NORTH DAKOTA', 'OH': 'OHIO', 'OK': 'OKLAHOMA', 'OR': 'OREGON', 'PA': 'PENNSYLVANIA',
    'RI': 'RHODE ISLAND', 'SC': 'SOUTH CAROLINA', 'SD': 'SOUTH DAKOTA', 'TN': 'TENNESSEE',
    'TX': 'TEXAS', 'UT': 'UTAH', 'VT': 'VERMONT', 'VA': 'VIRGINIA', 'WA': 'WASHINGTON',
    'WV': 'WEST VIRGINIA', 'WI': 'WISCONSIN', 'WY': 'WYOMING', 'DC': 'DISTRICT OF COLUMBIA',
    'PR': 'PUERTO RICO', 'VI': 'VIRGIN ISLANDS'
}

@login_required
def generate_report(request):
    """
    Handles the 'Generate Report' page.
    - GET: Displays dashboards for state and courier statistics.
    - POST: Generates and serves an Excel file based on the selected month.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("You do not have permission to access this resource.")

    # --- POST: Handle Excel Report Generation ---
    if request.method == 'POST':
        month_str = request.POST.get('month')
        margin_str = request.POST.get('margin', '20')
        rate_str = request.POST.get('usd_exchange_rate', '7.25')
        try:
            year, month = map(int, month_str.split('-'))
            _, last_day_of_month = calendar.monthrange(year, month)
            start_date = datetime.date(year, month, 1)
            end_date = datetime.date(year, month, last_day_of_month)
            margin_multiplier = 1 + (Decimal(margin_str) / Decimal('100'))
            exchange_rate = Decimal(rate_str)
            if exchange_rate <= 0:
                return HttpResponse("Exchange rate must be positive.", status=400)
        except (ValueError, TypeError):
            return HttpResponse("Invalid data provided.", status=400)

        parcels = Parcel.objects.filter(created_at__date__range=[start_date, end_date]).select_related(
            'order__warehouse', 'packaging_type', 'billing_item').order_by('created_at')
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Parcels Export"
        headers = ["Order#", "Tracking Number", "Shipment Date", "Warehouse", "Type", "Shipment cost", "Dispute"]
        sheet.append(headers)
        for parcel in parcels:
            shipment_charges = Decimal('0.00')
            is_disputed = ""
            try:
                if parcel.billing_item:
                    if parcel.billing_item.actual_cost is not None:
                        billing_cost = parcel.billing_item.actual_cost
                        shipment_charges = (billing_cost / exchange_rate) * margin_multiplier
                    if parcel.billing_item.dispute_date:
                        is_disputed = "Yes"
            except Parcel.billing_item.RelatedObjectDoesNotExist:
                pass
            row_data = [
                parcel.order.erp_order_id, parcel.tracking_number, parcel.created_at.strftime('%Y-%m-%d'),
                parcel.order.warehouse.name if parcel.order and parcel.order.warehouse else 'N/A',
                parcel.packaging_type.name if parcel.packaging_type else 'N/A', round(shipment_charges, 2),
                is_disputed
            ]
            sheet.append(row_data)
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="parcels_report_{month_str}.xlsx"'
        workbook.save(response)
        return response

    # --- GET: Handle Dashboard Display ---
    today = datetime.date.today()
    selected_month_str = request.GET.get('month', today.strftime('%Y-%m'))
    try:
        selected_date = datetime.datetime.strptime(selected_month_str, '%Y-%m').date()
    except ValueError:
        selected_date = today

    months_for_selector = []
    current_month = today.replace(day=1)
    for _ in range(12):
        months_for_selector.append(current_month.strftime('%Y-%m'))
        last_month = current_month - datetime.timedelta(days=1)
        current_month = last_month.replace(day=1)

    # Dashboard 1: State Statistics
    stats_query_state = Parcel.objects.filter(created_at__year=selected_date.year, created_at__month=selected_date.month).values(
        'billing_item__receiver_state', 'courier_company__name'
    ).annotate(total=Count('id')).order_by('billing_item__receiver_state', '-total')
    state_courier_counts = {}
    for item in stats_query_state:
        raw_state = item['billing_item__receiver_state']
        courier = item['courier_company__name']
        count = item['total']
        if not raw_state or not courier:
            continue
        state = STATE_MAP.get(raw_state.upper(), raw_state.upper())
        if state not in state_courier_counts:
            state_courier_counts[state] = {'total': 0, 'couriers': {}}
        state_courier_counts[state]['couriers'][courier] = count
        state_courier_counts[state]['total'] += count

    # Dynamically generate the list of all couriers
    all_couriers = list(CourierCompany.objects.values_list('name', flat=True))

    # Dashboard 2: Courier Performance Statistics
    # **FIXED**: Using the direct 'courier_company__name' and correct status logic
    courier_stats_query = Parcel.objects.filter(
        created_at__year=selected_date.year, created_at__month=selected_date.month
    ).values('courier_company__name').annotate(
        total_parcels=Count('id'),
        total_successful=Count('id', filter=Q(status='DELIVERED')),
        total_billing_cost=Sum('actual_shipping_cost'), # Using direct field from Parcel
        total_delivery_duration=Sum(
            ExpressionWrapper(F('delivered_at') - F('shipped_at'), output_field=DurationField()),
            filter=Q(status='DELIVERED')
        )
    )

    monthly_stats_dict = {
        stat['courier_company__name']: stat
        for stat in courier_stats_query if stat['courier_company__name']
    }

    courier_performance_stats = []
    for courier_name in all_couriers:
        stat = monthly_stats_dict.get(courier_name)

        if stat:
            total_parcels = stat['total_parcels']
            total_successful = stat['total_successful']
            success_rate = (total_successful / total_parcels * 100) if total_parcels > 0 else 0
            avg_days = 0
            if total_successful > 0 and stat['total_delivery_duration']:
                avg_days = stat['total_delivery_duration'].total_seconds() / (3600 * 24) / total_successful
            total_billing_cost = stat['total_billing_cost'] or 0
        else:
            total_parcels, total_successful, success_rate, avg_days, total_billing_cost = 0, 0, 0, 0, 0

        courier_performance_stats.append({
            'courier_name': courier_name,
            'total_parcels': total_parcels,
            'total_successful': total_successful,
            'success_rate': success_rate,
            'avg_delivery_days': avg_days,
            'total_billing_cost': total_billing_cost,
        })

    context = {
        'state_courier_counts': state_courier_counts,
        'courier_performance_stats': courier_performance_stats,
        'months': months_for_selector,
        'selected_month': selected_date.strftime('%Y-%m')
    }

    return render(request, 'operation/generate_report.html', context)
