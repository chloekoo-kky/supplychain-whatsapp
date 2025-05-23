# app/operation/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.db.models import Q, Count, Prefetch
from django.utils.dateparse import parse_date
from datetime import datetime
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger # Add Paginator

import logging

import openpyxl
import xlrd

# Ensure your form is imported
from .forms import ExcelImportForm, ParcelForm, ParcelItemFormSet, InitialParcelItemFormSet
from .models import Order, OrderItem, Parcel, ParcelItem # generate_parcel_code is in Parcel.save()
from inventory.models import Product, process_order_allocation, InventoryBatchItem, StockTransaction
from warehouse.models import Warehouse, WarehouseProduct

logger = logging.getLogger(__name__)
DEFAULT_CUSTOMER_ORDERS_TAB = "customer_orders"
DEFAULT_PARCELS_TAB = "parcels_details"


@login_required
def order_list_view(request):
    active_tab = request.GET.get('tab', DEFAULT_CUSTOMER_ORDERS_TAB)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    is_filter_action = request.GET.get('is_filter_action') == 'true' # Check for our custom param

    warehouses = Warehouse.objects.all().order_by('name')
    status_choices = Order.STATUS_CHOICES
    import_form = ExcelImportForm()

    # Prepare base context
    context = {
        'warehouses': warehouses,
        'status_choices': status_choices,
        'import_form': import_form,
        'active_tab': active_tab,
        'DEFAULT_CUSTOMER_ORDERS_TAB': DEFAULT_CUSTOMER_ORDERS_TAB,
        'DEFAULT_PARCELS_TAB': DEFAULT_PARCELS_TAB,
    }

    if active_tab == DEFAULT_CUSTOMER_ORDERS_TAB:
        orders_qs = Order.objects.select_related(
            'warehouse',
            'imported_by'
        ).prefetch_related(
            Prefetch('items', queryset=OrderItem.objects.select_related('product', 'suggested_batch_item', 'warehouse_product').order_by('product__name')),
            Prefetch('parcels', queryset=Parcel.objects.all().order_by('-created_at'))
        ).all()

        selected_warehouse_id = request.GET.get('warehouse')
        selected_status = request.GET.get('status')
        query = request.GET.get('q', '').strip()
        page_number = request.GET.get('page', 1)

        if selected_warehouse_id:
            orders_qs = orders_qs.filter(warehouse_id=selected_warehouse_id)
        if selected_status:
            orders_qs = orders_qs.filter(status=selected_status)
        if query:
            orders_qs = orders_qs.filter(
                Q(erp_order_id__icontains=query) |
                Q(customer_name__icontains=query) |
                Q(order_display_code__icontains=query) |
                Q(items__product__sku__icontains=query) |
                Q(items__product__name__icontains=query) |
                Q(parcels__parcel_code_system__icontains=query) |
                Q(parcels__tracking_number__icontains=query)
            ).distinct()

        orders_qs = orders_qs.order_by('-order_date', '-imported_at')

        paginator = Paginator(orders_qs, 20)
        try:
            orders_page = paginator.page(page_number)
        except PageNotAnInteger:
            orders_page = paginator.page(1)
        except EmptyPage:
            orders_page = paginator.page(paginator.num_pages)

        context.update({
            'orders': orders_page,
            'total_orders_count': paginator.count, # Pass total count for non-AJAX and initial AJAX tab load
            'selected_warehouse': selected_warehouse_id,
            'selected_status': selected_status,
            'query': query,
            'page_title': "Customer Orders",
        })

        if is_ajax:
            if is_filter_action: # If it's a filter action (signaled by JS)
                response = render(request, 'operation/partials/_customer_orders_list_items_only.html', context)
                response['X-Total-Orders-Count'] = paginator.count # Add header for JS to update count
                return response
            else: # Initial AJAX load of the tab
                return render(request, 'operation/partials/customer_orders_table.html', context)

    elif active_tab == DEFAULT_PARCELS_TAB:
        parcels_qs = Parcel.objects.select_related(
            'order__warehouse',
            'order__imported_by',
            'created_by'
        ).prefetch_related(
            Prefetch('items_in_parcel', queryset=ParcelItem.objects.select_related('order_item__product', 'shipped_from_batch__warehouse_product__product'))
        ).all()

        selected_warehouse_id = request.GET.get('parcel_warehouse')
        query = request.GET.get('parcel_q', '').strip()
        page_number = request.GET.get('page', 1) # Pagination for parcels tab

        if selected_warehouse_id:
            parcels_qs = parcels_qs.filter(order__warehouse_id=selected_warehouse_id)
        if query:
            parcels_qs = parcels_qs.filter(
                Q(parcel_code_system__icontains=query) |
                # ... (other parcel query filters)
                Q(items_in_parcel__order_item__product__name__icontains=query)
            ).distinct()

        parcels_qs = parcels_qs.order_by('-created_at')
        parcel_paginator = Paginator(parcels_qs, 20)
        try:
            parcels_page = parcel_paginator.page(page_number)
        except PageNotAnInteger:
            parcels_page = parcel_paginator.page(1)
        except EmptyPage:
            parcels_page = parcel_paginator.page(parcel_paginator.num_pages)

        context.update({
            'parcels': parcels_page,
            'total_parcels_count': parcel_paginator.count, # For parcel count if needed
            'selected_parcel_warehouse': selected_warehouse_id,
            'parcel_query': query,
            'page_title': "Parcel Details",
        })
        if is_ajax:
             return render(request, 'operation/partials/parcels_table.html', context)

    return render(request, 'operation/order_management_base.html', context)


@login_required
def import_orders_from_excel(request):
    if request.method == 'POST':
        form = ExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            excel_file = request.FILES['excel_file']
            file_name_lower = excel_file.name.lower()

            try:
                workbook_data = None
                sheet = None
                is_xlsx = False

                if file_name_lower.endswith('.xlsx'):
                    workbook_data = openpyxl.load_workbook(excel_file, data_only=True)
                    sheet = workbook_data.active
                    is_xlsx = True
                elif file_name_lower.endswith('.xls'):
                    file_contents = excel_file.read()
                    workbook_data = xlrd.open_workbook(file_contents=file_contents)
                    sheet = workbook_data.sheet_by_index(0) # Assuming first sheet for .xls
                    is_xlsx = False
                else:
                    messages.error(request, "Unsupported file format. Please upload .xlsx or .xls files.")
                    return redirect('operation:order_list')

                headers = []
                data_rows_iterator = None

                if is_xlsx:
                    if sheet.max_row > 0:
                        headers = [cell.value for cell in sheet[1]] # First row for headers
                        data_rows_iterator = sheet.iter_rows(min_row=2, values_only=True) # Data from second row
                    else:
                        headers = [] # No headers if only one row or empty
                else: # xlrd for .xls
                    if sheet.nrows > 0:
                        headers = [sheet.cell_value(0, col_idx) for col_idx in range(sheet.ncols)]
                        def xlrd_rows_iterator(sheet_obj, book_datemode): # Pass datemode
                            for r_idx in range(1, sheet_obj.nrows):
                                row_values_xls = []
                                for c_idx in range(sheet_obj.ncols):
                                    cell_type = sheet_obj.cell_type(r_idx, c_idx)
                                    cell_value = sheet_obj.cell_value(r_idx, c_idx)
                                    if cell_type == xlrd.XL_CELL_DATE:
                                        # Use datemode from the workbook
                                        date_tuple = xlrd.xldate_as_datetime(cell_value, book_datemode)
                                        row_values_xls.append(date_tuple)
                                    elif cell_type == xlrd.XL_CELL_NUMBER and cell_value == int(cell_value): # Check if it's a whole number
                                        row_values_xls.append(int(cell_value))
                                    else:
                                        row_values_xls.append(cell_value)
                                yield row_values_xls
                        data_rows_iterator = xlrd_rows_iterator(sheet, workbook_data.datemode) # Pass datemode
                    else:
                        headers = []

                if not headers:
                     messages.error(request, "The Excel file is empty or has no header row.")
                     return redirect('operation:order_list')

                header_mapping_config = {
                    # Internal Key : Excel Header Name (case-insensitive from your file)
                    'erp_order_id': 'order id',
                    'order_date': 'order date',
                    'warehouse_name': 'warehouse name',
                    'customer_name': 'address name',
                    'company_name': 'company',
                    'address_line1': 'address',
                    'country': 'country',
                    'city': 'city',
                    'state': 'state',
                    'zip_code': 'zip',
                    'phone': 'phone',
                    'vat_number': 'vat number',
                    'product_name_from_excel': 'product name', # Key for product identifier from Excel
                    'quantity_ordered': 'product quantity',
                    'is_cold': 'iscold',
                    'title_notes': 'title',
                    'comment_notes': 'comment',
                }

                normalized_actual_headers = {str(h).strip().lower(): str(h).strip() for h in headers if h is not None}
                header_map_for_indexing = {} # Stores: internal_key -> original_excel_header_case
                missing_headers_from_config = []
                # These keys are considered essential for creating an order or order item
                critical_internal_keys = ['erp_order_id', 'order_date', 'warehouse_name', 'product_name_from_excel', 'quantity_ordered']

                for internal_key, excel_header_normalized_target in header_mapping_config.items():
                    found_actual_header = None
                    # Find the original Excel header that matches the normalized target
                    for actual_header_normalized, actual_header_original_case in normalized_actual_headers.items():
                        if actual_header_normalized == excel_header_normalized_target.lower(): # Ensure target is lower for comparison
                            found_actual_header = actual_header_original_case
                            break
                    if found_actual_header:
                        header_map_for_indexing[internal_key] = found_actual_header
                    elif internal_key in critical_internal_keys:
                         missing_headers_from_config.append(f"'{excel_header_normalized_target}' (expected for '{internal_key}')")


                if missing_headers_from_config:
                    messages.error(request, f"Required headers not found in Excel: {', '.join(missing_headers_from_config)}. Available headers found: {', '.join(filter(None,headers))}")
                    return redirect('operation:order_list')

                # Create a map from internal key to actual column index in the file
                final_header_to_index_map = {}
                for internal_key, mapped_excel_header_original_case in header_map_for_indexing.items():
                    try:
                        # Get index from the original `headers` list (maintaining case from file)
                        idx = headers.index(mapped_excel_header_original_case)
                        final_header_to_index_map[internal_key] = idx
                    except ValueError:
                        # This should not happen if header_map_for_indexing was built correctly from found headers
                        messages.error(request, f"Configuration error: Mapped Excel header '{mapped_excel_header_original_case}' (for internal key '{internal_key}') not found in the original headers list. This is an internal logic error.")
                        return redirect('operation:order_list')

                orders_data = {} # Holds structured data: {erp_id: {'order_details': {}, 'items': []}}
                last_valid_erp_order_id = None # To handle multi-line items for the same order

                for row_idx, row_tuple in enumerate(data_rows_iterator, start=2): # enumerate from 2 because row 1 is header
                    # Skip row if all cells are empty or just whitespace
                    if not any(str(cell_val).strip() for cell_val in row_tuple if cell_val is not None):
                        logger.debug(f"Row {row_idx}: Skipped. Entirely empty or whitespace.")
                        continue

                    # Helper to get value from current row using internal_key
                    def get_current_row_value(internal_key_lambda, default=None): # Renamed internal_key to avoid conflict
                        idx = final_header_to_index_map.get(internal_key_lambda)
                        if idx is not None and idx < len(row_tuple) and row_tuple[idx] is not None:
                            val_lambda = row_tuple[idx]
                            if isinstance(val_lambda, (datetime, int, float)): # Return numeric/date types as is initially
                                return val_lambda
                            val_str = str(val_lambda).strip()
                            return val_str if val_str != "" else default
                        return default


                    current_row_erp_order_id = get_current_row_value('erp_order_id')
                    # Use current row's Order ID if present, otherwise fallback to last valid one for multi-line items
                    erp_order_id_to_use = current_row_erp_order_id if current_row_erp_order_id else last_valid_erp_order_id

                    if not erp_order_id_to_use:
                        messages.warning(request, f"Row {row_idx}: Skipped. Missing Order ID and no previous order context.")
                        logger.warning(f"Row {row_idx}: Skipped. Missing Order ID.")
                        continue # Cannot process item without an Order ID
                    if current_row_erp_order_id: # Update last_valid_erp_order_id if a new one is explicitly found
                        last_valid_erp_order_id = current_row_erp_order_id

                    # Ensure erp_order_id_to_use is string for dictionary keys and model field
                    erp_order_id_to_use = str(erp_order_id_to_use)


                    # These are item-specific, must exist for each item row
                    product_name_excel = get_current_row_value('product_name_from_excel')
                    quantity_ordered_str = get_current_row_value('quantity_ordered')

                    if not all([product_name_excel, quantity_ordered_str]):
                        messages.warning(request, f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Missing critical item data (Product Name or Quantity). Skipping item.")
                        logger.warning(f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Missing Product Name ('{product_name_excel}') or Qty ('{quantity_ordered_str}'). Skipping item.")
                        continue

                    # Order-level details are typically taken from the first line of an order group
                    order_date_for_this_entry = None
                    if erp_order_id_to_use not in orders_data: # This is the first item for this order
                        order_date_val = get_current_row_value('order_date')
                        warehouse_name = get_current_row_value('warehouse_name')
                        customer_name = get_current_row_value('customer_name')

                        # Date parsing logic
                        if order_date_val:
                            if isinstance(order_date_val, datetime):
                                order_date_for_this_entry = order_date_val.date()
                            elif isinstance(order_date_val, str):
                                order_date_str_cleaned = order_date_val.split(' ')[0] # Handle "YYYY-MM-DD HH:MM:SS"
                                order_date_for_this_entry = parse_date(order_date_str_cleaned)
                                if not order_date_for_this_entry: # Try other common formats
                                    for fmt in ('%B %d, %Y', '%b %d %Y', '%d/%m/%Y', '%m/%d/%Y', '%Y%m%d'): # Add more as needed
                                        try:
                                            order_date_for_this_entry = datetime.strptime(order_date_val, fmt).date()
                                            if order_date_for_this_entry: break
                                        except ValueError:
                                            continue
                            elif isinstance(order_date_val, (float, int)) and hasattr(workbook_data, 'datemode') and not is_xlsx: # xlrd specific for Excel date numbers
                                try:
                                    order_date_for_this_entry = xlrd.xldate_as_datetime(order_date_val, workbook_data.datemode).date()
                                except: pass # Ignore if conversion fails

                            if not order_date_for_this_entry and order_date_val: # If a value was there but couldn't be parsed
                                logger.warning(f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Unparseable order_date '{order_date_val}'.")

                        # Check for essential order details for the first item
                        if not all([order_date_for_this_entry, warehouse_name, customer_name]):
                            error_parts = []
                            if not order_date_for_this_entry: error_parts.append("Order Date (missing or invalid format)")
                            if not warehouse_name: error_parts.append("Warehouse Name")
                            if not customer_name: error_parts.append("Customer Name (Address Name)")
                            msg = f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Missing essential order details for first line: {', '.join(error_parts)}. Skipping order."
                            messages.warning(request, msg)
                            logger.warning(msg)
                            last_valid_erp_order_id = None # Reset context as this order is invalid
                            continue

                        orders_data[erp_order_id_to_use] = {
                            'order_details': {
                                'erp_order_id': str(erp_order_id_to_use), # Ensure it's string
                                'order_date': order_date_for_this_entry,
                                'warehouse_name': warehouse_name,
                                'customer_name': customer_name,
                                'company_name': get_current_row_value('company_name'),
                                'recipient_address_line1': get_current_row_value('address_line1'),
                                'recipient_address_city': get_current_row_value('city'),
                                'recipient_address_state': get_current_row_value('state'),
                                'recipient_address_zip': get_current_row_value('zip_code'),
                                'recipient_address_country': get_current_row_value('country'),
                                'recipient_phone': get_current_row_value('phone'),
                                'vat_number': get_current_row_value('vat_number'),
                                'is_cold_chain': False, # Initialized, will be updated by items
                                'title_notes': get_current_row_value('title_notes'),
                                'shipping_notes': get_current_row_value('comment_notes'),
                            },
                            'items': []
                        }
                    # else: order_date_for_this_entry is already set from the first item of this order group

                    # Quantity processing for the current item
                    try:
                        quantity_ordered = int(float(str(quantity_ordered_str))) # str() for robustness if it's already float/int
                        if quantity_ordered <= 0:
                            raise ValueError("Quantity must be positive.")
                    except (ValueError, TypeError):
                        msg = f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Invalid quantity '{quantity_ordered_str}' for item '{product_name_excel}'. Skipping item."
                        messages.warning(request, msg)
                        logger.warning(msg)
                        continue

                    is_cold_str = get_current_row_value('is_cold')
                    is_cold = str(is_cold_str).strip().lower() == 'true' if is_cold_str else False

                    if is_cold: # If any item is cold, the whole order is cold chain
                        orders_data[erp_order_id_to_use]['order_details']['is_cold_chain'] = True

                    orders_data[erp_order_id_to_use]['items'].append({
                        'product_name_from_excel': str(product_name_excel), # Ensure it's stored as string
                        'quantity_ordered': quantity_ordered,
                        'is_cold_item': is_cold,
                    })
                # End of row processing loop

                # Database operations
                with transaction.atomic():
                    created_orders_count = 0
                    updated_orders_count = 0
                    created_items_count = 0
                    skipped_orders_db = 0 # Orders skipped during DB interaction
                    skipped_items_db = 0  # Items skipped during DB interaction

                    for erp_id_key, data_dict in orders_data.items():
                        # erp_id_key is already a string here because it was used as a dict key
                        order_details_map = data_dict['order_details']

                        try:
                            warehouse = Warehouse.objects.get(name__iexact=order_details_map['warehouse_name'])
                        except Warehouse.DoesNotExist:
                            messages.error(request, f"Order ID {erp_id_key}: Warehouse '{order_details_map['warehouse_name']}' not found during DB operations. Skipping order.")
                            logger.error(f"Order ID {erp_id_key}: Warehouse '{order_details_map['warehouse_name']}' not found.")
                            skipped_orders_db += 1
                            continue

                        # Prepare defaults for Order model, ensuring erp_order_id is string
                        order_field_defaults = {
                            k: v for k, v in order_details_map.items()
                            if k not in ['erp_order_id', 'warehouse_name'] and hasattr(Order, k) # Filter for valid Order fields
                        }
                        # Explicitly ensure erp_order_id in defaults is string if it was part of order_details_map keys used for defaults
                        # However, erp_order_id is usually the lookup key, not in defaults directly.

                        order_field_defaults['warehouse'] = warehouse
                        order_field_defaults['status'] = 'PENDING_ALLOCATION' # Default status for new/updated orders
                        order_field_defaults['imported_by'] = request.user if request.user.is_authenticated else None

                        # Ensure erp_order_id for lookup is the string version
                        current_order_erp_id_str = str(order_details_map['erp_order_id'])

                        order, created = Order.objects.update_or_create(
                            erp_order_id=current_order_erp_id_str, # Use string for lookup
                            defaults=order_field_defaults
                        )

                        # Ensure the instance's erp_order_id is also string before any save() call
                        order.erp_order_id = current_order_erp_id_str

                        if created:
                            created_orders_count += 1
                        else: # If updating, clear previous items to replace them cleanly
                            order.items.all().delete()
                            updated_orders_count +=1

                        current_order_items_processed_db = 0
                        for item_data in data_dict['items']:
                            # product_name_from_excel is already string from parsing
                            product_identifier_from_excel = item_data['product_name_from_excel']
                            product = None
                            try: # Try matching by SKU first, then by name
                                product = Product.objects.get(sku__iexact=product_identifier_from_excel)
                            except Product.DoesNotExist:
                                try:
                                    product = Product.objects.get(name__iexact=product_identifier_from_excel)
                                except Product.DoesNotExist:
                                    messages.error(request, f"Order ID {erp_id_key}, Item Identifier '{product_identifier_from_excel}': Product not found by SKU or Name. Skipping item.")
                                    logger.error(f"Order ID {erp_id_key}, Item '{product_identifier_from_excel}': Product not found by SKU/Name.")
                                    skipped_items_db +=1
                                    continue
                                except Product.MultipleObjectsReturned:
                                    messages.error(request, f"Order ID {erp_id_key}, Item Name '{product_identifier_from_excel}': Multiple products found by this name. Use unique SKU or ensure unique names. Skipping item.")
                                    logger.error(f"Order ID {erp_id_key}, Item '{product_identifier_from_excel}': Multiple products by name.")
                                    skipped_items_db +=1
                                    continue
                            except Product.MultipleObjectsReturned: # Ambiguous SKU
                                messages.error(request, f"Order ID {erp_id_key}, SKU '{product_identifier_from_excel}': Multiple products found with this SKU. SKUs must be unique. Skipping item.")
                                logger.error(f"Order ID {erp_id_key}, SKU '{product_identifier_from_excel}': Multiple products by SKU.")
                                skipped_items_db +=1
                                continue

                            # Link to WarehouseProduct
                            warehouse_product_instance = WarehouseProduct.objects.filter(product=product, warehouse=warehouse).first()
                            if not warehouse_product_instance:
                                messages.warning(request, f"Order ID {erp_id_key}, Item '{product.sku}': WarehouseProduct link not found for warehouse '{warehouse.name}'. Item added without specific stock link.")
                                logger.warning(f"Order ID {erp_id_key}, Item '{product.sku}': WarehouseProduct link not found for WH '{warehouse.name}'.")

                            # Defaults for OrderItem
                            oi_defaults = {
                                'quantity_ordered': item_data['quantity_ordered'],
                                'erp_product_name': product_identifier_from_excel, # Storing the identifier used from Excel
                                'is_cold_item': item_data.get('is_cold_item', False),
                                'status': 'NEW', # Default for new items
                                'warehouse_product': warehouse_product_instance # This can be None if not found
                            }
                            OrderItem.objects.create(order=order, product=product, **oi_defaults)
                            current_order_items_processed_db +=1

                        created_items_count += current_order_items_processed_db
                        order.save() # This will trigger Order.save() which has the erp_order_id string conversion safeguard
                        process_order_allocation(order) # Process allocation for this order

                # After loop, prepare summary message
                final_message_parts = []
                if created_orders_count > 0: final_message_parts.append(f"Orders Created: {created_orders_count}")
                if updated_orders_count > 0: final_message_parts.append(f"Orders Updated: {updated_orders_count}")
                if created_items_count > 0: final_message_parts.append(f"Order Items Processed: {created_items_count}")

                if not final_message_parts and (skipped_orders_db == 0 and skipped_items_db == 0):
                    # This case means no new orders were made, and no existing ones were updated (or had items changed).
                    # It could be that the file contained only orders that already existed identically.
                    final_message_parts.append("No new orders or items to import based on ERP IDs. Existing orders might have been updated if their content changed.")

                if skipped_orders_db > 0: final_message_parts.append(f"Orders Skipped (DB): {skipped_orders_db}")
                if skipped_items_db > 0: final_message_parts.append(f"Items Skipped (DB): {skipped_items_db}")

                if final_message_parts:
                    messages.success(request, "Import process complete. " + ", ".join(final_message_parts) + ".")
                else: # Should only happen if orders_data was empty after parsing phase
                    messages.info(request, "No data found in the Excel file to process orders.")


            except ValueError as ve: # Catch issues from parsing (e.g., quantity conversion) earlier
                messages.error(request, f"Error in file structure or critical content: {str(ve)}")
                logger.error(f"Import ValueError: {str(ve)}", exc_info=True)
            except xlrd.XLRDError as xe: # For .xls specific errors
                messages.error(request, f"Error reading .xls file. It might be corrupted or an incompatible version: {str(xe)}")
                logger.error(f"Import XLRDError: {str(xe)}", exc_info=True)
            except Exception as e: # Generic catch-all for other unexpected issues
                messages.error(request, f"An unexpected error occurred during the import process: {str(e)}")
                logger.error(f"Import Exception: {str(e)}", exc_info=True)

            return redirect('operation:order_list')
        else: # Form not valid (e.g., no file uploaded, or wrong file type by browser)
            for field, errors_list in form.errors.items(): # Iterate through form errors
                for error in errors_list:
                    messages.error(request, f"Error in field '{form.fields[field].label if field != '__all__' else 'Form'}': {error}")
    # If GET request (page initially loaded) or form invalid, redirect back to list where form is displayed in modal
    return redirect('operation:order_list')



@login_required
def get_order_items_for_packing(request, order_pk):
    order = get_object_or_404(Order.objects.prefetch_related(
        Prefetch('items', queryset=OrderItem.objects.filter(
            Q(status='ALLOCATED') | Q(status='READY_TO_PACK') | Q(status='PACKED') # Consider items already packed for display
        ).select_related('product', 'suggested_batch_item').order_by('product__name'))
    ), pk=order_pk)

    # Basic permission check (can be more granular)
    if not request.user.is_superuser and (not request.user.warehouse or order.warehouse != request.user.warehouse):
        return JsonResponse({'success': False, 'message': 'Permission denied for this order.'}, status=403)

    initial_form_data = []
    for item in order.items.all():
        # Only include items that still need packing or are already packed (for editing quantities in a parcel)
        # quantity_remaining_to_pack could be negative if overallocated somehow, ensure it's at least 0
        qty_to_suggest_for_packing = max(0, item.quantity_allocated - item.quantity_packed)
        if qty_to_suggest_for_packing > 0 or item.status == 'PACKED': # Include if needs packing or is already packed
            initial_form_data.append({
                'order_item_id': item.pk,
                'product_name': item.product.name if item.product else item.erp_product_name,
                'sku': item.product.sku if item.product else "N/A",
                'quantity_to_pack': qty_to_suggest_for_packing,
                'selected_batch_item_id': item.suggested_batch_item.pk if item.suggested_batch_item else None,
                # For available_batches, we need to construct choices or let the form do it
            })

    packing_items_formset = InitialParcelItemFormSet(initial=initial_form_data, prefix='packitems')

    # Render the formset into HTML to send back
    formset_html = render_to_string(
        'operation/partials/pack_order_formset.html',
        {'formset': packing_items_formset, 'order': order},
        request=request
    )

    return JsonResponse({
        'success': True,
        'order_id': order.pk,
        'erp_order_id': order.erp_order_id,
        'customer_name': order.customer_name,
        'formset_html': formset_html
    })


@login_required
@transaction.atomic
def process_packing_for_order(request, order_pk):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)

    order = get_object_or_404(Order, pk=order_pk)
    if not request.user.is_superuser and (not request.user.warehouse or order.warehouse != request.user.warehouse):
        return JsonResponse({'success': False, 'message': 'Permission denied for this order.'}, status=403)

    parcel_form = ParcelForm(request.POST, prefix='parcel')
    # The item formset data needs to be constructed from request.POST based on 'packitems' prefix
    # This requires careful handling of formset management data (TOTAL_FORMS, INITIAL_FORMS etc.)

    num_item_forms = int(request.POST.get('packitems-TOTAL_FORMS', 0))
    items_to_pack_data = []
    valid_items_found = False

    for i in range(num_item_forms):
        order_item_id = request.POST.get(f'packitems-{i}-order_item_id')
        qty_to_pack_str = request.POST.get(f'packitems-{i}-quantity_to_pack')
        batch_id_str = request.POST.get(f'packitems-{i}-selected_batch_item_id') # Or from available_batches select

        if not order_item_id or not qty_to_pack_str: continue # Skip if essential data missing

        try:
            order_item = OrderItem.objects.get(pk=order_item_id, order=order)
            qty_to_pack = int(qty_to_pack_str)
            if qty_to_pack < 0: continue # Skip negative quantities
            if qty_to_pack == 0 and not request.POST.get(f'packitems-{i}-DELETE'): # Only skip if not marked for deletion
                continue

            valid_items_found = True
            batch_item = None
            if batch_id_str:
                try: batch_item = InventoryBatchItem.objects.get(pk=batch_id_str, warehouse_product=order_item.warehouse_product)
                except InventoryBatchItem.DoesNotExist:
                    # This case means a batch was selected that doesn't match the order item's product/warehouse, or doesn't exist
                    # It's an integrity issue, should ideally be prevented by JS filtering in the modal
                    return JsonResponse({'success': False, 'message': f"Invalid batch selected for item {order_item.product.sku}. Please refresh and try again."}, status=400)

            # Check if trying to pack more than available or allocated
            remaining_to_pack_for_item = order_item.quantity_allocated - order_item.quantity_packed
            if qty_to_pack > remaining_to_pack_for_item:
                return JsonResponse({'success': False, 'message': f"Cannot pack {qty_to_pack} of {order_item.product.sku}. Only {remaining_to_pack_for_item} remaining to pack from allocation."}, status=400)

            # Check batch quantity if a batch is selected
            if batch_item and qty_to_pack > batch_item.quantity:
                return JsonResponse({'success': False, 'message': f"Batch {batch_item.batch_number} for {order_item.product.sku} only has {batch_item.quantity} available, tried to pack {qty_to_pack}."}, status=400)

            items_to_pack_data.append({
                'order_item': order_item,
                'quantity': qty_to_pack,
                'batch': batch_item
            })
        except (OrderItem.DoesNotExist, ValueError):
            # Log this error, could be tampering or stale form
            logger.warning(f"Invalid item data submitted for order {order_pk}, item ID {order_item_id} or qty {qty_to_pack_str}")
            continue # Skip this item

    if not valid_items_found:
        return JsonResponse({'success': False, 'message': 'No valid items were selected for packing.'}, status=400)

    if parcel_form.is_valid():
        parcel = parcel_form.save(commit=False)
        parcel.order = order
        parcel.created_by = request.user
        parcel.save() # This will generate parcel_code_system

        total_packed_qty_for_order = 0
        for item_data in items_to_pack_data:
            ParcelItem.objects.create(
                parcel=parcel,
                order_item=item_data['order_item'],
                quantity_shipped_in_this_parcel=item_data['quantity'], # This is "packed into this parcel"
                shipped_from_batch=item_data['batch']
            )
            # Update OrderItem's quantity_packed
            oi = item_data['order_item']
            oi.quantity_packed += item_data['quantity']

            # Update batch quantity if a batch was used
            if item_data['batch']:
                batch_instance = item_data['batch']
                batch_instance.quantity -= item_data['quantity']
                batch_instance.save(update_fields=['quantity'])

                # Create StockTransaction for deduction from batch
                StockTransaction.objects.create(
                    warehouse=batch_instance.warehouse_product.warehouse,
                    warehouse_product=batch_instance.warehouse_product,
                    product=batch_instance.warehouse_product.product,
                    transaction_type='OUT', # Or a more specific "PACKED_FOR_SHIPMENT"
                    quantity=-item_data['quantity'], # Negative for deduction
                    reference_note=f"Packed for Order {order.erp_order_id}, Parcel {parcel.parcel_code_system}",
                    related_order=order,
                    # related_batch_item=batch_instance # If you add this field to StockTransaction
                )

            if oi.quantity_packed >= oi.quantity_ordered: # If all ordered qty for this item is now packed
                oi.status = 'PACKED'
            elif oi.quantity_packed > 0: # If some, but not all, is packed
                oi.status = 'READY_TO_PACK' # Or 'PARTIALLY_PACKED'
            oi.save()
            total_packed_qty_for_order += item_data['quantity']

        # Update overall order status
        order_fully_packed = all(item.status == 'PACKED' or item.status == 'SHIPPED' or item.quantity_ordered == 0 for item in order.items.all())
        if order_fully_packed:
            order.status = 'PENDING_SHIPMENT' # All items are packed, ready for the courier
        elif total_packed_qty_for_order > 0 : # Some items were packed
            order.status = 'READY_FOR_PACKING' # Or 'PARTIALLY_PACKED_OVERALL'
        order.save()

        messages.success(request, f"Parcel {parcel.parcel_code_system} created for order {order.erp_order_id} with {len(items_to_pack_data)} item line(s).")
        return JsonResponse({'success': True, 'message': 'Parcel created successfully!', 'redirect_url': request.build_absolute_uri(reverse('operation:order_list')) + f"?tab={DEFAULT_PARCELS_TAB}"})
    else:
        # Parcel form is not valid
        errors = {f"parcel_{field}": error[0] for field, error in parcel_form.errors.items()}
        return JsonResponse({'success': False, 'message': 'Please correct parcel details.', 'errors': errors}, status=400)

@login_required
def get_available_batches_for_order_item(request, order_item_pk):
    order_item = get_object_or_404(OrderItem.objects.select_related('product', 'warehouse_product__warehouse'), pk=order_item_pk)

    if not request.user.is_superuser and (not request.user.warehouse or order_item.order.warehouse != request.user.warehouse):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    if not order_item.warehouse_product:
        return JsonResponse({'batches': []}) # No specific stock item to pick batches from

    batches_qs = InventoryBatchItem.objects.filter(
        warehouse_product=order_item.warehouse_product,
        quantity__gt=0 # Only batches with stock
    ).order_by(F('pick_priority').asc(nulls_last=True), F('expiry_date').asc(nulls_last=True), 'date_received')

    batches_data = [{
        'id': batch.pk,
        'display_name': f"B: {batch.batch_number or 'N/A'} L: {batch.location_label or 'N/A'} Exp: {batch.expiry_date or 'N/A'} (Qty: {batch.quantity}) {'[DEF]' if batch.pick_priority == 0 else ''}{'[SEC]' if batch.pick_priority == 1 else ''}"
    } for batch in batches_qs]

    return JsonResponse({'batches': batches_data})
