from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.utils.dateparse import parse_date
from datetime import datetime

import openpyxl
import xlrd

# Ensure your form is imported
from .forms import ExcelImportForm
from .models import Order, OrderItem # generate_parcel_code is in Order.save()
from inventory.models import Product
from warehouse.models import Warehouse, WarehouseProduct


@login_required
def order_list_view(request):
    orders_qs = Order.objects.select_related(
        'warehouse',
        'imported_by'
    ).prefetch_related(
        'items__product',  # Prefetch product for each item
        'items__suggested_batch_item', # Prefetch the suggested batch item
        'items__suggested_batch_item__warehouse_product__product', # If you need product info from the batch's WP
        'items__suggested_batch_item__warehouse_product__warehouse' # If you need warehouse info from the batch's WP
    ).all()
    # --- Filtering ---
    selected_warehouse_id = request.GET.get('warehouse')
    selected_status = request.GET.get('status')
    query = request.GET.get('q', '').strip()

    if selected_warehouse_id:
        orders_qs = orders_qs.filter(warehouse_id=selected_warehouse_id)
    if selected_status:
        orders_qs = orders_qs.filter(status=selected_status)

    if query:
        orders_qs = orders_qs.filter(
            Q(erp_order_id__icontains=query) |
            Q(customer_name__icontains=query) |
            Q(parcel_code__icontains=query) |
            Q(items__product__sku__icontains=query) |
            Q(items__product__name__icontains=query)
        ).distinct()

    warehouses = Warehouse.objects.all().order_by('name')
    status_choices = Order.STATUS_CHOICES

    # Instantiate the import form to be used in the modal
    import_form = ExcelImportForm()

    context = {
        'orders': orders_qs.order_by('-order_date', '-imported_at'),
        'warehouses': warehouses,
        'status_choices': status_choices,
        'selected_warehouse': selected_warehouse_id,
        'selected_status': selected_status,
        'query': query,
        'page_title': "Customer Orders",
        'import_form': import_form, # Pass the form to the template
    }
    return render(request, 'operation/order_list.html', context)


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
                    sheet = workbook_data.sheet_by_index(0)
                    is_xlsx = False
                else:
                    messages.error(request, "Unsupported file format. Please upload .xlsx or .xls files.")
                    return redirect('operation:order_list') # Redirect to list view

                headers = []
                data_rows_iterator = None

                if is_xlsx:
                    if sheet.max_row > 0:
                        headers = [cell.value for cell in sheet[1]]
                        data_rows_iterator = sheet.iter_rows(min_row=2, values_only=True)
                    else: headers = []
                else: # xlrd
                    if sheet.nrows > 0:
                        headers = [sheet.cell_value(0, col_idx) for col_idx in range(sheet.ncols)]
                        def xlrd_rows_iterator(sheet_obj):
                            for r_idx in range(1, sheet_obj.nrows):
                                row_values_xls = []
                                for c_idx in range(sheet_obj.ncols):
                                    cell_type = sheet_obj.cell_type(r_idx, c_idx)
                                    cell_value = sheet_obj.cell_value(r_idx, c_idx)
                                    if cell_type == xlrd.XL_CELL_DATE:
                                        date_tuple = xlrd.xldate_as_datetime(cell_value, workbook_data.datemode)
                                        row_values_xls.append(date_tuple)
                                    elif cell_type == xlrd.XL_CELL_NUMBER and cell_value == int(cell_value):
                                        row_values_xls.append(int(cell_value))
                                    else:
                                        row_values_xls.append(cell_value)
                                yield row_values_xls
                        data_rows_iterator = xlrd_rows_iterator(sheet)
                    else: headers = []

                if not headers:
                     messages.error(request, "The Excel file is empty or has no header row.")
                     return redirect('operation:order_list')

                # This mapping tells the code which Excel column name corresponds to which internal data key
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
                    'product_name_from_excel': 'product name', # CHANGED: Key to reflect it's a name
                    'quantity_ordered': 'product quantity',
                    'is_cold': 'iscold',
                    'title_notes': 'title',
                    'comment_notes': 'comment',
                    # 'batch_detail': 'batch detail' # Add if you need to parse this
                }

                normalized_actual_headers = {str(h).strip().lower(): str(h).strip() for h in headers if h is not None}

                header_map_for_indexing = {}
                missing_headers_from_config = []

                for internal_key, excel_header_normalized_target in header_mapping_config.items():
                    # Find the original Excel header that matches the normalized target
                    found_actual_header = None
                    for actual_header_normalized, actual_header_original_case in normalized_actual_headers.items():
                        if actual_header_normalized == excel_header_normalized_target:
                            found_actual_header = actual_header_original_case
                            break

                    if found_actual_header:
                        header_map_for_indexing[internal_key] = found_actual_header
                    else:
                        # Define which keys from header_mapping_config are absolutely critical
                        critical_internal_keys = ['erp_order_id', 'order_date', 'warehouse_name', 'product_name_from_excel', 'quantity_ordered']
                        if internal_key in critical_internal_keys:
                             missing_headers_from_config.append(f"'{excel_header_normalized_target}' (expected for {internal_key})")

                if missing_headers_from_config:
                    messages.error(request, f"Required headers not found in Excel: {', '.join(missing_headers_from_config)}. Available headers: {', '.join(filter(None,headers))}")
                    return redirect('operation:order_list')

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

                orders_data = {}
                last_valid_erp_order_id = None

                for row_idx, row_tuple in enumerate(data_rows_iterator, start=2):
                    if not any(str(cell_val).strip() for cell_val in row_tuple if cell_val is not None):
                        continue

                    def get_current_row_value(internal_key, default=None):
                        idx = final_header_to_index_map.get(internal_key)
                        if idx is not None and idx < len(row_tuple) and row_tuple[idx] is not None and str(row_tuple[idx]).strip() != "":
                            val = row_tuple[idx]
                            return str(val).strip() if not isinstance(val, (datetime, int, float)) else val
                        return default

                    current_row_erp_order_id = get_current_row_value('erp_order_id')
                    if current_row_erp_order_id:
                        erp_order_id_to_use = current_row_erp_order_id
                        last_valid_erp_order_id = current_row_erp_order_id
                    elif last_valid_erp_order_id:
                        erp_order_id_to_use = last_valid_erp_order_id
                    else:
                        messages.warning(request, f"Row {row_idx}: Skipped. Missing Order ID and no previous order context.")
                        continue

                    order_date_val = get_current_row_value('order_date')
                    warehouse_name = get_current_row_value('warehouse_name')
                    customer_name = get_current_row_value('customer_name')

                    # Item-specific fields
                    product_name_excel = get_current_row_value('product_name_from_excel') # Get product name from Excel
                    quantity_ordered_str = get_current_row_value('quantity_ordered')

                    company_name = get_current_row_value('company_name')
                    address_line1 = get_current_row_value('address_line1')
                    country = get_current_row_value('country')
                    city = get_current_row_value('city')
                    state = get_current_row_value('state')
                    zip_code = get_current_row_value('zip_code')
                    phone = get_current_row_value('phone')
                    vat_number = get_current_row_value('vat_number')
                    is_cold_str = get_current_row_value('is_cold')
                    title_notes = get_current_row_value('title_notes')
                    comment_notes = get_current_row_value('comment_notes')

                    if not all([product_name_excel, quantity_ordered_str]):
                        messages.warning(request, f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Missing critical item data (Product Name or Quantity). Skipping item.")
                        continue

                    order_date_for_this_entry = None
                    if order_date_val:
                        if isinstance(order_date_val, datetime): order_date_for_this_entry = order_date_val.date()
                        elif isinstance(order_date_val, str):
                            order_date_str_cleaned = order_date_val.split(' ')[0]
                            order_date_for_this_entry = parse_date(order_date_str_cleaned)
                            if not order_date_for_this_entry:
                                try: order_date_for_this_entry = datetime.strptime(order_date_val, '%B %d, %Y').date()
                                except ValueError:
                                    try: order_date_for_this_entry = datetime.strptime(order_date_val, '%b %d %Y').date()
                                    except ValueError: pass
                        elif isinstance(order_date_val, (float, int)) and workbook_data and hasattr(workbook_data, 'datemode'):
                            try: order_date_for_this_entry = xlrd.xldate_as_datetime(order_date_val, workbook_data.datemode).date()
                            except: pass
                        if not order_date_for_this_entry and order_date_val: # If value was present but not parsable
                             messages.warning(request, f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Invalid date format '{order_date_val}'.")
                             # It will try to use order date from the first line of the order later.

                    try:
                        quantity_ordered = int(float(quantity_ordered_str))
                        if quantity_ordered <= 0: raise ValueError("Quantity must be positive.")
                    except (ValueError, TypeError):
                        messages.warning(request, f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Invalid quantity '{quantity_ordered_str}'. Skipping item.")
                        continue

                    is_cold = str(is_cold_str).strip().lower() == 'true' if is_cold_str else False

                    if erp_order_id_to_use not in orders_data:
                        if not order_date_for_this_entry:
                            messages.warning(request, f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Order Date is missing/invalid for the first line of this order. Skipping order.")
                            last_valid_erp_order_id = None; continue
                        if not warehouse_name:
                             messages.warning(request, f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Warehouse Name is missing for the first line. Skipping order.")
                             last_valid_erp_order_id = None; continue
                        if not customer_name: # Assuming customer name is essential for a new order
                             messages.warning(request, f"Row {row_idx} (Order ID: {erp_order_id_to_use}): Customer Name is missing for the first line. Skipping order.")
                             last_valid_erp_order_id = None; continue

                        orders_data[erp_order_id_to_use] = {
                            'order_details': {
                                'erp_order_id': erp_order_id_to_use, 'order_date': order_date_for_this_entry,
                                'warehouse_name': warehouse_name, 'customer_name': customer_name,
                                'company_name': company_name, 'recipient_address_line1': address_line1,
                                'recipient_address_city': city, 'recipient_address_state': state,
                                'recipient_address_zip': zip_code, 'recipient_address_country': country,
                                'recipient_phone': phone, 'vat_number': vat_number,
                                'is_cold_chain': False, 'title_notes': title_notes, 'shipping_notes': comment_notes,
                            }, 'items': []
                        }

                    if is_cold: orders_data[erp_order_id_to_use]['order_details']['is_cold_chain'] = True

                    orders_data[erp_order_id_to_use]['items'].append({
                        'product_name_from_excel': product_name_excel, # Store the name from Excel
                        'quantity_ordered': quantity_ordered,
                        'is_cold_item': is_cold,
                        # 'erp_product_name': product_name_excel # Can be used for OrderItem.erp_product_name
                    })

                with transaction.atomic():
                    created_orders_count = 0; updated_orders_count = 0; created_items_count = 0; skipped_orders = 0; skipped_items = 0
                    for erp_id, data_dict in orders_data.items():
                        order_details_map = data_dict['order_details']
                        if not all([order_details_map.get('order_date'), order_details_map.get('warehouse_name'), order_details_map.get('customer_name')]):
                            messages.error(request, f"Order ID {erp_id}: Cannot save order due to missing essential details. Skipping.")
                            skipped_orders += 1; continue
                        try: warehouse = Warehouse.objects.get(name__iexact=order_details_map['warehouse_name'])
                        except Warehouse.DoesNotExist:
                            messages.error(request, f"Order ID {erp_id}: Warehouse '{order_details_map['warehouse_name']}' not found. Skipping.")
                            skipped_orders += 1; continue

                        order_defaults = {
                            'order_date': order_details_map['order_date'], 'customer_name': order_details_map['customer_name'],
                            'company_name': order_details_map.get('company_name'),
                            'recipient_address_line1': order_details_map.get('recipient_address_line1'),
                            'recipient_address_city': order_details_map.get('recipient_address_city'),
                            'recipient_address_state': order_details_map.get('recipient_address_state'),
                            'recipient_address_zip': order_details_map.get('recipient_address_zip'),
                            'recipient_address_country': order_details_map.get('recipient_address_country'),
                            'recipient_phone': order_details_map.get('recipient_phone'),
                            'vat_number': order_details_map.get('vat_number'),
                            'title_notes': order_details_map.get('title_notes'),
                            'shipping_notes': order_details_map.get('shipping_notes'),
                            'warehouse': warehouse, 'is_cold_chain': order_details_map.get('is_cold_chain', False),
                            'status': 'PENDING_ALLOCATION', 'imported_by': request.user if request.user.is_authenticated else None,
                        }
                        order_defaults_cleaned = {k: v for k, v in order_defaults.items() if v is not None or k in ['customer_name']}
                        order, created = Order.objects.update_or_create(erp_order_id=erp_id, defaults=order_defaults_cleaned)
                        if created: created_orders_count += 1
                        else: updated_orders_count +=1

                        current_order_items_created_or_updated = 0
                        for item_data in data_dict['items']:
                            product_name_to_match = item_data['product_name_from_excel']
                            try:
                                # Attempt to match by product name (case-insensitive)
                                product = Product.objects.get(name__iexact=product_name_to_match)
                            except Product.DoesNotExist:
                                messages.error(request, f"Order ID {erp_id}, Product Name '{product_name_to_match}': Product not found in database. Skipping item.")
                                skipped_items +=1; continue
                            except Product.MultipleObjectsReturned:
                                messages.error(request, f"Order ID {erp_id}, Product Name '{product_name_to_match}': Multiple products found with this name. Please ensure product names are unique or use SKUs. Skipping item.")
                                skipped_items +=1; continue

                            oi_defaults = {
                                'quantity_ordered': item_data['quantity_ordered'],
                                'erp_product_name': product_name_to_match, # Store the name from Excel
                                'is_cold_item': item_data.get('is_cold_item', False), 'status': 'PENDING_ALLOCATION',
                                'warehouse_product': WarehouseProduct.objects.filter(product=product, warehouse=warehouse).first()
                            }
                            oi_defaults_cleaned = {k:v for k,v in oi_defaults.items() if v is not None}
                            order_item, item_created_or_updated_flag = OrderItem.objects.update_or_create(order=order, product=product, defaults=oi_defaults_cleaned)
                            current_order_items_created_or_updated +=1
                        created_items_count += current_order_items_created_or_updated
                        order.save()

                final_message = f"Successfully processed file. Orders Created: {created_orders_count}, Orders Updated: {updated_orders_count}, Order Items Handled: {created_items_count}."
                if skipped_orders > 0: final_message += f" Orders Skipped: {skipped_orders}."
                if skipped_items > 0: final_message += f" Items Skipped: {skipped_items}."
                messages.success(request, final_message)

            except ValueError as ve: messages.error(request, f"Error processing file structure: {str(ve)}")
            except xlrd.XLRDError as xe: messages.error(request, f"Error reading .xls file: {str(xe)}.")
            except Exception as e: messages.error(request, f"Unexpected error during import: {str(e)}")

            return redirect('operation:order_list')
        else:
            for field, errors_list in form.errors.items():
                for error in errors_list:
                    messages.error(request, f"Error in field '{form.fields[field].label if field != '__all__' else 'Form'}': {error}")
            return redirect('operation:order_list')

    return redirect('operation:order_list')
