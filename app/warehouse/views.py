# ===== warehouse/views.py =====
import pprint
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest, JsonResponse, HttpResponse, HttpResponseForbidden
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.serializers import serialize
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import QuerySet
import logging


from django.views.decorators.http import require_POST
from django.db import transaction
from django.utils import timezone
from django.urls import reverse # For generating URLs in JSON response


from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt


from django.db.models import Q

from django.template.loader import render_to_string

from .models import (
    WarehouseProduct, Warehouse, PurchaseOrder, PurchaseOrderItem,
    PurchaseOrderReceiptLog, PurchaseOrderReceiptItem # New models
)
from inventory.models import Product, Supplier, StockTransaction # Make sure StockTransaction is imported

from .utils import get_next_status


DEFAULT_TAB = "warehouseproduct"
logger = logging.getLogger(__name__)

# --- Helper function to prepare context for PO rendering ---
def _get_po_context_for_rendering(purchase_orders_page_obj):
    """Helper to generate context needed for rendering PO lists and modals."""
    status_choices_list = PurchaseOrder.STATUS_CHOICES
    status_dates_dict = {}
    next_statuses_dict = {}
    # Generates field names like 'draft_date', 'waiting_invoice_date', etc.
    status_field_names = [code.lower() + "_date" for code, _ in status_choices_list]

    for po in purchase_orders_page_obj: # Iterate over the Paginator page object items
        po_id_str = str(po.id)
        dates = {}
        for field_name_base in status_field_names:
            status_code_key = field_name_base.replace('_date', '').upper() # e.g., DRAFT
            date_val = getattr(po, field_name_base, None)
            if date_val and hasattr(date_val, 'strftime'):
                dates[status_code_key] = date_val.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if po.last_updated_date else None,
            else:
                dates[status_code_key] = None # Or handle backfilling here if desired
        status_dates_dict[po_id_str] = dates
        next_statuses_dict[po_id_str] = get_next_status(po.status)

    return {
        "status_choices": status_choices_list,
        "status_dates": status_dates_dict,
        "next_statuses": next_statuses_dict,
    }

# --- Tab Handler for Warehouse Products ---
def handle_warehouseproduct_tab(request):
    user = request.user
    selected_warehouse_id = request.GET.get("warehouse")
    selected_supplier_id = request.GET.get("supplier")
    query_param = request.GET.get("q", "").strip()

    logger.debug(f"handle_warehouseproduct_tab: warehouse='{selected_warehouse_id}', supplier='{selected_supplier_id}', q='{query_param}' for user '{user.email}' (superuser: {user.is_superuser})")

    base_products_qs = WarehouseProduct.objects.select_related('product', 'warehouse', 'supplier')

    if user.is_superuser:
        products_qs = base_products_qs.all()
        if selected_warehouse_id:
            products_qs = products_qs.filter(warehouse_id=selected_warehouse_id)

        warehouses_list = Warehouse.objects.all().order_by('name')
        # For superuser, list all suppliers
        suppliers_list = Supplier.objects.all().order_by('code')

    else: # Non-superuser
        if user.warehouse:
            products_qs = base_products_qs.filter(warehouse=user.warehouse)
            warehouses_list = Warehouse.objects.filter(pk=user.warehouse.id)

            # SIMPLIFIED: Get suppliers directly from WarehouseProduct entries in their warehouse
            suppliers_list = Supplier.objects.filter(
            warehouseproduct__warehouse=user.warehouse,
            warehouseproduct__supplier__isnull=False # Ensure we only get WPs that have a supplier
            ).distinct().order_by('code')

        else: # Non-superuser with no assigned warehouse
            products_qs = base_products_qs.none()
            warehouses_list = Warehouse.objects.none()
            suppliers_list = Supplier.objects.none()

    # Apply supplier filter (common for both)
    if selected_supplier_id:
            products_qs = products_qs.filter(supplier_id=selected_supplier_id) # Now only checks WarehouseProduct.supplier



    # Apply text query (common for both)
    if query_param:
        q_filters = Q(product__name__icontains=query_param) | \
                    Q(product__sku__icontains=query_param)
        if user.is_superuser and not selected_warehouse_id: # Only allow superuser to search by warehouse name if not filtering
            q_filters |= Q(warehouse__name__icontains=query_param)
        products_qs = products_qs.filter(q_filters).distinct()

    products_qs = products_qs.order_by('product__name')

    context = {
        "products": products_qs,
        "warehouses": warehouses_list,
        "suppliers": suppliers_list,
        "selected_warehouse_id": selected_warehouse_id,
        "selected_supplier": selected_supplier_id, # This is the ID of the selected supplier
        "query": query_param,
        "user": user,
        "today": timezone.now().date(),
    }
    return context, "warehouse/warehouse_product_partial.html"

# --- Tab Handler for Purchase Orders ---
def handle_purchaseorders_tab(request):
    logger.debug("--- handle_purchaseorders_tab ---")
    query = request.GET.get("q", "").strip()
    selected_supplier_id = request.GET.get("supplier")
    selected_status = request.GET.get("status")
    raw_page = request.GET.get('page', '1')
    try:
        page = int(raw_page)
        if page <= 0: page = 1
    except ValueError:
        page = 1

    logger.debug(f"Params: query='{query}', supplier='{selected_supplier_id}', status='{selected_status}', page={page}")

    purchase_orders_qs = PurchaseOrder.objects.select_related('supplier') \
                                       .prefetch_related(
                                           'items__item__product', # WarehouseProduct's product
                                           'items__item__warehouse', # WarehouseProduct's warehouse
                                           'items__item__supplier' # WarehouseProduct's supplier
                                        ) \
                                       .order_by('-last_updated_date')

    if selected_supplier_id:
        purchase_orders_qs = purchase_orders_qs.filter(supplier_id=selected_supplier_id)
    if selected_status:
        purchase_orders_qs = purchase_orders_qs.filter(status=selected_status)
    if query:
        q_filters = Q(supplier__name__icontains=query) | \
                    Q(items__item__product__name__icontains=query) | \
                    Q(items__item__product__sku__icontains=query)
        if query.isdigit(): # Safe check for ID
            q_filters |= Q(id=query)
        purchase_orders_qs = purchase_orders_qs.filter(q_filters).distinct()

    paginator = Paginator(purchase_orders_qs, 10) # Show 10 POs per page
    try:
        purchase_orders_page = paginator.page(page)
    except PageNotAnInteger:
        purchase_orders_page = paginator.page(1)
    except EmptyPage:
        purchase_orders_page = paginator.page(paginator.num_pages)

    common_po_rendering_context = _get_po_context_for_rendering(purchase_orders_page)

    context = {
        "purchase_orders": purchase_orders_page, # This is the Paginator page object
        "suppliers": Supplier.objects.all(), # For filter dropdown
        "selected_supplier": selected_supplier_id,
        "selected_status": selected_status,
        "query": query,
        "page_obj": purchase_orders_page, # Common practice for paginator page object
        "has_next": purchase_orders_page.has_next(),
        "next_page": purchase_orders_page.next_page_number() if purchase_orders_page.has_next() else None,
        **common_po_rendering_context # Add status_choices, status_dates, next_statuses
    }
    return context, "warehouse/purchase_orders_partial.html"

TAB_HANDLERS = {
    "warehouseproduct": handle_warehouseproduct_tab,
    "purchaseorders": handle_purchaseorders_tab, # Make sure handle_purchaseorders_tab is defined
}

@login_required
def warehouse_management(request):
    tab = request.GET.get("tab", DEFAULT_TAB)
    handler = TAB_HANDLERS.get(tab, handle_warehouseproduct_tab) # Default to warehouseproduct handler

    logger.debug(f"--- warehouse_management view --- Request GET: {request.GET}, Selected tab: {tab}, Handler: {handler.__name__ if handler else 'None'}")

    context, partial_template = handler(request)
    context["active_tab"] = tab
    # context["user"] = request.user # Already passed by handle_warehouseproduct_tab if needed for its partial

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        logger.debug(f"Rendering partial: {partial_template} for AJAX request. Context keys: {list(context.keys())}")
        return render(request, partial_template, context)

    logger.debug(f"Rendering full page: warehouse/warehouse_management.html, active_tab: {tab}")
    return render(request, "warehouse/warehouse_management.html", context)

def get_next_status(current_status): # Make sure this function is defined
    ordered_statuses = ['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED', 'DELIVERED']
    try:
        idx = ordered_statuses.index(current_status)
        if current_status in ['DELIVERED', 'CANCELLED']: return None
        return ordered_statuses[idx + 1] if idx + 1 < len(ordered_statuses) else None
    except ValueError:
        return None

# @login_required
# def warehouse_management(request):
#     tab = request.GET.get("tab", "warehouseproduct")
#     user = request.user
#     selected_supplier = request.GET.get("supplier")
#     selected_status = request.GET.get("status")


#     if tab == "purchaseorders":

#         purchase_orders = PurchaseOrder.objects \
#             .annotate(
#                 latest_update=Coalesce(
#                     F('delivered_date'),
#                     F('partially_delivered_date'),
#                     F('payment_made_date'),
#                     F('waiting_invoice_date'),
#                     F('draft_date'),

#                 )
#             ).order_by('-last_updated_date') \
#             .prefetch_related("items__item__product")

#         if selected_supplier:
#             purchase_orders = purchase_orders.filter(supplier_id=selected_supplier)
#         if selected_status:
#             purchase_orders = purchase_orders.filter(status=selected_status)

#         status_choices = PurchaseOrder.STATUS_CHOICES
#         ordered_statuses = [code for code, _ in status_choices]

#         paginator = Paginator(purchase_orders, 10)  # 每页5个
#         page_number = request.GET.get("page")
#         purchase_orders_page = paginator.get_page(page_number)


#         status_dates = {
#             po.id: {
#                 code: getattr(po, f"{code.lower()}_date", None)
#                 for code in ordered_statuses
#             }
#             for po in purchase_orders_page
#         }

#         next_statuses = {
#             po.id: get_next_status(po.status)
#             for po in purchase_orders_page
#         }

#         query = request.GET.get("q", "").strip()
#         if query:
#             purchase_orders_page = purchase_orders_page.filter(
#                 Q(id__icontains=query) |
#                 Q(supplier__name__icontains=query) |
#                 Q(items__item__product__name__icontains=query) |
#                 Q(items__item__product__sku__icontains=query)
#             ).distinct()


#         context = {
#             "purchase_orders": purchase_orders_page,
#             "suppliers": Supplier.objects.all(),
#             "selected_supplier": selected_supplier,
#             "selected_status": selected_status,
#             "status_choices": status_choices,
#             "status_dates": status_dates,
#             "next_statuses": next_statuses,
#             "active_tab": "purchaseorders",
#             "query": query,
#             "page_obj": purchase_orders_page,
#         }

#         partial_template = "warehouse/purchase_orders_partial.html"

#     else:
#         if user.is_superuser:
#             selected_warehouse_id = request.GET.get("warehouse")
#             selected_supplier_id = request.GET.get("supplier")

#             products = WarehouseProduct.objects.all().order_by('product__name')  # 一开始先查全部

#             if selected_warehouse_id:
#                 products = products.filter(warehouse_id=selected_warehouse_id)

#             if selected_supplier_id:
#                 products = products.filter(product__supplier_id=selected_supplier_id)

#             warehouses = Warehouse.objects.all()
#             suppliers = Supplier.objects.all()
#         else:
#             products = WarehouseProduct.objects.filter(warehouse=user.warehouse).order_by('product__name')
#             warehouses = None
#             suppliers = None
#             selected_warehouse_id = None
#             selected_supplier_id = None

#         context = {
#             "products": products,
#             "warehouse": user.warehouse if not user.is_superuser else None,
#             "warehouses": warehouses,
#             "suppliers": suppliers,  # ✅ 补上
#             "selected_warehouse_id": selected_warehouse_id,
#             "selected_supplier_id": selected_supplier_id,
#             "user": user,
#             "active_tab": "warehouseproduct",
#         }
#         partial_template = "warehouse/warehouse_product_partial.html"

#     if request.headers.get("x-requested-with") == "XMLHttpRequest":
#         return render(request, partial_template, context)

#     return render(request, "warehouse/warehouse_management.html", context)


@login_required
def update_stock(request):
    query = request.GET.get('q', '').strip()
    products = WarehouseProduct.objects.all()

    if query:
        products = products.filter(
            Q(product__sku__icontains=query) |
            Q(product__name__icontains=query) |
            Q(warehouse__name__icontains=query)
        )

    if request.method == "POST":
        for key, value in request.POST.items():
            if key.startswith("quantity_"):
                pk = key.split("_")[1]
                try:
                    wp = WarehouseProduct.objects.get(pk=pk)
                    wp.quantity = int(value)
                    wp.batch_number = request.POST.get(f"batch_number_{pk}", wp.batch_number)
                    wp.save()
                except WarehouseProduct.DoesNotExist:
                    continue

        return redirect('warehouse:update_stock')

    context = {
        'products': products,
        'query': query,
    }
    return render(request, 'warehouse/update_stock.html', context)


@login_required
def search(request):
    print("--- Entering AJAX search view (/warehouse/search/) ---")
    query = request.GET.get('q', '').strip()
    warehouse_id_param = request.GET.get('warehouse') # Explicit warehouse filter from GET params
    supplier_id = request.GET.get('supplier')
    user = request.user

    print(f"Received parameters: q='{query}', warehouse_id_param='{warehouse_id_param}', supplier_id='{supplier_id}', user='{user.email}'")

    # Start with a base queryset
    products_qs = WarehouseProduct.objects.select_related('product', 'warehouse', 'supplier')

    if user.is_superuser:
        # Superuser can filter by any warehouse if 'warehouse' param is provided
        if warehouse_id_param:
            products_qs = products_qs.filter(warehouse_id=warehouse_id_param)
            print(f"Superuser: After warehouse_id_param filter ('{warehouse_id_param}'), count: {products_qs.count()}")
        # If no warehouse_id_param, superuser sees all warehouses initially (before other filters like query or supplier)
    else:
        # Non-superuser is RESTRICTED to their assigned warehouse
        if user.warehouse:
            products_qs = products_qs.filter(warehouse=user.warehouse)
            print(f"Non-superuser: Filtered by assigned warehouse '{user.warehouse.name}', count: {products_qs.count()}")
        else:
            # Non-superuser with no assigned warehouse should see no products
            products_qs = products_qs.none()
            print("Non-superuser: No warehouse assigned, queryset is None.")

    # Apply supplier filter (if provided) - This applies to both superuser and non-superuser on the (potentially already warehouse-filtered) queryset
    if supplier_id:
        # Ensure the supplier filter on WarehouseProduct is correct.
        # If WarehouseProduct has a direct FK 'supplier':
        products_qs = products_qs.filter(supplier_id=supplier_id)
        # If supplier is linked via product (Product.supplier):
        # products_qs = products_qs.filter(product__supplier_id=supplier_id)
        # Choose the correct one based on your WarehouseProduct model structure.
        # From your models, WarehouseProduct has a direct 'supplier' FK.
        print(f"After supplier_id filter ('{supplier_id}'), count: {products_qs.count()}")

    # Apply text query filter
    if query:
        print(f"Applying text query: '{query}'")
        q_object = Q(product__name__icontains=query) | \
                   Q(product__sku__icontains=query)

        # For superusers, if they are NOT already filtering by a specific warehouse_id_param,
        # allow them to search by warehouse name as well.
        # For non-superusers, they are already confined to their warehouse, so searching its name is less critical here.
        if user.is_superuser and not warehouse_id_param:
            q_object |= Q(warehouse__name__icontains=query)

        products_qs = products_qs.filter(q_object).distinct()
        print(f"After text query filter ('{query}'), count: {products_qs.count()}")

    # Optional: Apply a default ordering
    products_qs = products_qs.order_by('product__name')

    # Logging for results (optional)
    if products_qs.exists():
        print("Sample results from AJAX search (first 3):")
        for p_item in products_qs[:3]: # Limit logging output
            print(f"  - Product: {p_item.product.name}, SKU: {p_item.product.sku}, Warehouse: {p_item.warehouse.name}")
    else:
        print("No results from AJAX search with current filters.")

    context = {
        "products": products_qs,
        "query": query, # Pass the original query back for display in the input field
        "today": timezone.now().date(), # For incoming PO ETA comparison in _search_results.html
        "user": user # Pass user to the template if needed for conditional rendering
    }
    return render(request, "warehouse/_search_results.html", context)

@login_required
def warehouseproduct_details(request, pk):
    # ... (Implementation from your file) ...
    try:
        wp = WarehouseProduct.objects.get(pk=pk)
        data = {"product": str(wp.product), # Calls __str__ method of Product model
            "product_sku": wp.product.sku if wp.product else "N/A",
            "product_name": wp.product.name if wp.product else "N/A",
            "warehouse": str(wp.warehouse), # Calls __str__ method of Warehouse model
            "warehouse_name": wp.warehouse.name if wp.warehouse else "N/A",
            # "expiry_date": "N/A", # Removed as WarehouseProduct no longer has expiry_date
                                     # Expiry is now on InventoryBatchItem
            "quantity": wp.quantity,
            "threshold": wp.threshold if wp.threshold is not None else 0,
            "supplier": str(wp.supplier) if wp.supplier else "N/A",
            # You can add more details from wp.product or wp.warehouse if needed
            # Example: Aggregate batch information if desired (more complex)
            # "total_batch_quantity": wp.total_quantity, # if you want to show aggregate from batches
        }
        return JsonResponse(data)
    except WarehouseProduct.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

@csrf_exempt # Consider if CSRF protection is needed and how to handle it with JS POSTs
@login_required
@require_POST
def prepare_po_from_selection(request):
    # ... (Implementation from your file) ...
    try:
        data = json.loads(request.body)
        selected_items = data.get("selected_items", [])
        healthy_multiplier = float(data.get("healthy_multiplier", 1.5))

        if not selected_items:
            return JsonResponse({"success": False, "message": "No products selected."})

        result = {}

        for wp_id in selected_items:
            try:
                wp = WarehouseProduct.objects.select_related('product', 'warehouse', 'supplier').get(id=wp_id)
                # Determine the supplier: WarehouseProduct's own supplier takes precedence,
                # otherwise, fall back to the Product's default supplier.
                supplier_to_use = wp.supplier # Directly use this

                if not supplier_to_use:
                    logger.warning(f"WarehouseProduct ID {wp_id} (SKU: {wp.product.sku}) has no associated supplier. Skipping for PO prep.")
                    continue

                if supplier_to_use.id not in result:
                    result[supplier_to_use.id] = {
                        "supplier_name": supplier_to_use.name,
                        "supplier_id": supplier_to_use.id,
                        "products": [],
                    }

                healthy_threshold = wp.threshold * healthy_multiplier
                suggested_qty = max(int(healthy_threshold - wp.quantity), 1) # Ensure at least 1 if below healthy

                result[supplier_to_use.id]["products"].append({
                    "warehouse_product_id": wp.id,
                    "product_name": wp.product.name,
                    "warehouse_name": wp.warehouse.name, # Useful for display if user manages multiple warehouses
                    "current_quantity": wp.quantity,
                    "threshold": wp.threshold,
                    "gap": suggested_qty,
                })
            except WarehouseProduct.DoesNotExist:
                logger.error(f"WarehouseProduct with ID {wp_id} not found during PO preparation.")
                continue

        if not result: # If no products could be mapped to suppliers
            return JsonResponse({"success": False, "message": "No products with valid suppliers found in selection."})

        return JsonResponse({"success": True, "data": list(result.values())})
    except Exception as e:
        logger.error(f"Error in prepare_po_from_selection: {e}", exc_info=True)
        return JsonResponse({"success": False, "message": str(e)})

@csrf_exempt # Consider CSRF
@login_required
@require_POST
@transaction.atomic
def confirm_create_po(request):
    # ... (Implementation from your file) ...
    try:
        data = json.loads(request.body)
        orders_data = data.get("orders", {}) # Renamed to avoid conflict with Order model

        if not orders_data:
            return JsonResponse({"success": False, "error": "No orders data provided."}, status=400)

        created_po_ids = []
        for supplier_id_str, items in orders_data.items():
            try:
                supplier_id = int(supplier_id_str)
                supplier = Supplier.objects.get(pk=supplier_id)
            except (ValueError, Supplier.DoesNotExist):
                logger.error(f"Invalid supplier ID: {supplier_id_str} in confirm_create_po.")
                # Optionally, decide if this should halt all PO creation or just skip this one
                return JsonResponse({"success": False, "error": f"Invalid supplier ID: {supplier_id_str}."}, status=400)

            # Create the PurchaseOrder instance
            # Ensure all necessary fields for PO creation are handled, e.g., assigned user, warehouse context if applicable
            po = PurchaseOrder.objects.create(
                supplier=supplier,
                status='DRAFT' # Default status
                # created_by=request.user, # Example if tracking user
                # warehouse=request.user.warehouse, # Example if PO is tied to a user's warehouse
            )
            created_po_ids.append(po.id)

            for item_data in items:
                try:
                    wp_id = int(item_data["warehouse_product_id"])
                    wp = WarehouseProduct.objects.get(id=wp_id)
                    quantity = int(item_data["quantity"])

                    if quantity <= 0: # Skip items with zero or negative quantity
                        logger.warning(f"Skipping item {wp.product.sku} for PO {po.id} due to zero/negative quantity: {quantity}")
                        continue

                    # Price: Use product's default price. Could be extended to allow price input.
                    price = wp.product.price

                    PurchaseOrderItem.objects.create(
                        purchase_order=po,
                        item=wp, # This is WarehouseProduct instance
                        quantity=quantity,
                        price=price,
                    )
                except (KeyError, ValueError, WarehouseProduct.DoesNotExist) as item_exc:
                    logger.error(f"Error processing item for PO (Supplier ID: {supplier_id}): {item_data}. Exception: {item_exc}", exc_info=True)
                    # Decide on error handling: roll back all, or just skip this item/PO?
                    # For now, let's assume an error with an item invalidates this PO creation and rolls back.
                    raise # Re-raise to trigger transaction rollback for this PO

        if not created_po_ids:
             return JsonResponse({"success": False, "error": "No Purchase Orders were created."})


        return JsonResponse({
            "success": True,
            "message": f"{len(created_po_ids)} Purchase Order(s) created.",
            "latest_po_id": created_po_ids[-1] if created_po_ids else None,
            "created_po_ids": created_po_ids
        })
    except Exception as e:
        logger.error(f"Error in confirm_create_po: {str(e)}", exc_info=True)
        # The @transaction.atomic will handle rollback on unhandled exceptions
        return JsonResponse({"success": False, "error": str(e)}, status=500)

@require_POST
@login_required # Ensure user is logged in
@transaction.atomic # Ensure atomicity
def po_update(request, pk):
    # ... (Implementation from your file, ensure csrf protection if not an API endpoint) ...
    # If this is called via AJAX from a form, ensure CSRF token is sent or exempt if appropriate
    purchase_order = get_object_or_404(PurchaseOrder, pk=pk)
    original_status = purchase_order.status
    selected_status = request.POST.get('selected_status')
    eta_str = request.POST.get('eta') # Expects 'YYYY-MM-DD'

    response_data = {
        'success': False,
        'message': 'No changes made or invalid request.',
        'po_id': purchase_order.id,
        'new_status_display': purchase_order.get_status_display(), # Start with current
        'refresh_po_list': False
    }
    updated_fields_count = 0

    if eta_str: # eta_str can be empty if user clears the date
        try:
            # Attempt to parse, or set to None if empty, or handle invalid format
            purchase_order.eta = eta_str if eta_str else None
            updated_fields_count +=1
        except ValueError:
            response_data['message'] = 'Invalid ETA date format. Please use YYYY-MM-DD.'
            # No JsonResponse here yet, continue to check status

    if selected_status and selected_status != original_status:
        if selected_status in [choice[0] for choice in PurchaseOrder.STATUS_CHOICES]:
            purchase_order.status = selected_status
            # The PurchaseOrder.save() method should handle set_status_date()
            updated_fields_count +=1
        else:
            response_data['message'] = f'Invalid status: {selected_status}.'
            # No JsonResponse here yet

    if updated_fields_count > 0:
        try:
            purchase_order.save() # This will call set_status_date if status changed
            response_data['success'] = True
            response_data['message'] = 'PO details updated successfully.'
            response_data['new_status_display'] = purchase_order.get_status_display()
            response_data['refresh_po_list'] = True # Signal JS to refresh the list
        except Exception as e:
            logger.error(f"Error saving PO {pk} in po_update: {e}", exc_info=True)
            response_data['success'] = False;
            response_data['message'] = f"Error saving PO: {e}"


    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse(response_data)

    # Fallback for non-AJAX (should ideally not happen with current JS)
    # messages.info(request, response_data['message']) # Use Django messages framework
    return redirect('warehouse:warehouse_management') # Redirect back to the main page

# --- New View: Get PO Items for Receiving Modal ---
@login_required
@login_required
def get_po_items_for_receiving(request, po_id):
    # ... (Implementation from your file) ...
    purchase_order = get_object_or_404(PurchaseOrder.objects.prefetch_related('items__item__product'), pk=po_id)
    if purchase_order.status not in ['PAYMENT_MADE', 'PARTIALLY_DELIVERED', 'DELIVERED']: # Only allow receiving for these statuses
        return JsonResponse({'success': False, 'message': 'Cannot receive items for PO in current status.'}, status=400)

    items_data = []
    for po_item in purchase_order.items.all():
        items_data.append({
            'po_item_id': po_item.id,
            'sku': po_item.item.product.sku,
            'name': po_item.item.product.name,
            'warehouse_product_id': po_item.item.id,
            'ordered_quantity': po_item.quantity,
            'already_received_quantity': po_item.received_quantity,
            'balance_quantity': po_item.balance_quantity,
        })
    return JsonResponse({'success': True, 'po_id': po_id, 'po_status': purchase_order.status, 'items': items_data})

# --- New View: Process PO Receipt ---
@login_required
@require_POST
@transaction.atomic
def process_po_receipt(request, po_id):
    # ... (Implementation from your file) ...
    # Ensure this view correctly updates WarehouseProduct quantities, StockTransaction,
    # PurchaseOrderItem.received_quantity, and the PurchaseOrder status.
    purchase_order = get_object_or_404(PurchaseOrder, pk=po_id)

    # Basic permission check (can be more granular)
    # if purchase_order.warehouse.owner != request.user and not request.user.is_staff:
    #     return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)
    if purchase_order.status not in ['PAYMENT_MADE', 'PARTIALLY_DELIVERED', 'DELIVERED']:
         return JsonResponse({'success': False, 'message': f'Cannot process receipt for PO in status {purchase_order.get_status_display()}.'}, status=400)


    try:
        data = json.loads(request.body)
        received_items_info = data.get('items', [])
        receipt_notes = data.get('notes', '')

        if not received_items_info and purchase_order.status != 'DELIVERED': # Allow empty submission if PO is already delivered (e.g. just adding notes)
            return JsonResponse({'success': False, 'message': 'No items data provided for receipt.'}, status=400)

        receipt_log = PurchaseOrderReceiptLog.objects.create(
            purchase_order=purchase_order,
            notes=receipt_notes
            # user=request.user # Optional: track who received
        )

        any_item_received_this_time = False
        for item_info in received_items_info:
            po_item_id = item_info.get('po_item_id')
            quantity_received_now = int(item_info.get('quantity_received_now', 0))

            if quantity_received_now < 0: # Cannot receive negative
                raise ValueError(f"Received quantity cannot be negative for PO Item ID {po_item_id}.")
            if quantity_received_now == 0: # Skip items with no quantity received this time
                continue

            any_item_received_this_time = True
            po_item = get_object_or_404(PurchaseOrderItem.objects.select_related('item__product', 'item__warehouse'), pk=po_item_id, purchase_order=purchase_order)

            if quantity_received_now > po_item.balance_quantity:
                raise ValueError(f"Cannot receive {quantity_received_now} for {po_item.item.product.name}. Max balance is {po_item.balance_quantity}.")

            PurchaseOrderReceiptItem.objects.create(
                receipt_log=receipt_log,
                po_item=po_item,
                quantity_received_this_time=quantity_received_now
            )

            warehouse_product = po_item.item # This is the WarehouseProduct instance
            warehouse_product.quantity += quantity_received_now
            warehouse_product.save()

            StockTransaction.objects.create(
                warehouse=warehouse_product.warehouse,
                warehouse_product=warehouse_product,
                product=warehouse_product.product, # Redundant for faster queries
                transaction_type='IN',
                quantity=quantity_received_now,
                reference_note=f"PO#{purchase_order.id} - Receipt#{receipt_log.id}",
                related_po=purchase_order
            )

            po_item.received_quantity += quantity_received_now
            po_item.save()

        if not any_item_received_this_time and purchase_order.status != 'DELIVERED':
            # If modal submitted with all zeros and PO wasn't already delivered
            receipt_log.delete() # Delete the empty log
            return JsonResponse({'success': True, 'message': 'No items were marked as received. PO status unchanged.', 'refresh_po_list': True})

        # Determine new PO status
        if purchase_order.is_fully_received():
            if purchase_order.status != 'DELIVERED': # Only change to DELIVERED if not already
                 purchase_order.status = 'DELIVERED'
        elif any_item_received_this_time: # If anything was received, but not all, it's PARTIALLY_DELIVERED
             if purchase_order.status != 'PARTIALLY_DELIVERED':
                  purchase_order.status = 'PARTIALLY_DELIVERED'

        # purchase_order.inventory_updated = True # This field was removed in migration 0006
        purchase_order.save() # This will also call set_status_date

        return JsonResponse({
            'success': True,
            'message': 'Items received and inventory updated successfully.',
            'new_po_status': purchase_order.get_status_display(),
            'po_id': purchase_order.id,
            'refresh_po_list': True
        })

    except ValueError as ve:
        logger.warning(f"ValueError in process_po_receipt for PO {po_id}: {ve}")
        return JsonResponse({'success': False, 'message': str(ve)}, status=400)
    except Exception as e:
        logger.error(f"Exception in process_po_receipt for PO {po_id}: {e}", exc_info=True)
        return JsonResponse({'success': False, 'message': f'An error occurred: {str(e)}'}, status=500)

@require_POST
@login_required
@transaction.atomic
def po_edit_items(request, pk):
    # ... (Implementation from your file, ensure csrf protection) ...
    # This view needs to handle formsets or a list of items being submitted.
    # The example below assumes simple list of item changes.
    # For complex formset handling, Django's formset processing is recommended.
    po = get_object_or_404(PurchaseOrder, pk=pk)

    if not request.user.is_superuser: # Or other permission check
        return HttpResponseForbidden("You are not authorized to edit this PO.")

    # This is a simplified example. Real implementation should use Django Forms/Formsets for validation and security.
    # Assuming request.POST contains item data like:
    # item_id_0, product_0, quantity_0, price_0
    # item_id_1, product_1, quantity_1, price_1
    # ...
    # And a way to identify new vs existing items, and items to delete.

    # For simplicity, let's assume a full replace based on submitted data.
    # A more robust solution would process changes (add, update, delete).

    submitted_item_pks = [] # Keep track of item PKs that were submitted to find deletions

    try:
        # Iterate through potential item rows (e.g., based on a known max or a 'total_forms' count)
        # This is a placeholder for actual form/formset processing logic
        # Example: Loop through a known number of forms or use management form data
        # This part of the code from the original file was very basic.
        # A robust solution would use Django Formsets.
        # The original had `total_rows = int(request.POST.get("total_rows", 0))` which is not standard.
        # Let's adapt it slightly, but highlight it's fragile.

        idx = 0
        while True: # Loop until no more item data for this index
            item_id_str = request.POST.get(f"item_id_{idx}")
            wp_id_str = request.POST.get(f"product_{idx}") # WarehouseProduct ID
            quantity_str = request.POST.get(f"quantity_{idx}")
            price_str = request.POST.get(f"price_{idx}")

            if not all([wp_id_str, quantity_str, price_str]): # If essential data for a row is missing, stop
                break

            wp_id = int(wp_id_str)
            quantity = int(quantity_str)
            price = float(price_str) # Consider using Decimal for price

            if quantity <= 0: # Ignore items with 0 or negative quantity
                idx += 1
                continue

            wp = get_object_or_404(WarehouseProduct, pk=wp_id)

            if item_id_str and item_id_str != 'None' and item_id_str != '': # Existing item
                try:
                    po_item = PurchaseOrderItem.objects.get(pk=int(item_id_str), purchase_order=po)
                    po_item.item = wp
                    po_item.quantity = quantity
                    po_item.price = price
                    po_item.save()
                    submitted_item_pks.append(po_item.pk)
                except PurchaseOrderItem.DoesNotExist:
                    # Item ID was provided but not found for this PO, treat as new or error
                    # For simplicity, let's assume it might be an error or ignore
                    logger.warning(f"POItem ID {item_id_str} not found for PO {po.id}, but data submitted.")
                    pass # Or create new if logic allows
            else: # New item
                new_po_item = PurchaseOrderItem.objects.create(
                    purchase_order=po,
                    item=wp,
                    quantity=quantity,
                    price=price
                )
                submitted_item_pks.append(new_po_item.pk)
            idx += 1

        # Delete items that were part of the PO but not in the submission (if submitted_item_pks is comprehensive)
        # This requires a hidden field for each existing item indicating if it should be deleted,
        # or by comparing existing items to `submitted_item_pks`.
        # For example, if a 'DELETE_{idx}' checkbox was used:
        # for i in range(total_forms_from_management_form):
        #    if request.POST.get(f'form-{i}-DELETE'):
        #        item_id_to_delete = request.POST.get(f'form-{i}-id')
        #        PurchaseOrderItem.objects.filter(pk=item_id_to_delete, purchase_order=po).delete()

        # Simpler delete: if an existing item's PK is not in submitted_item_pks, delete it.
        # This assumes all existing items are re-submitted if they are to be kept/updated.
        for item_in_db in po.items.all():
            if item_in_db.pk not in submitted_item_pks:
                item_in_db.delete()

        po.save() # To update last_updated_date etc.

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            # Instead of re-rendering the whole partial, could return JSON success
            # and let JS refresh the specific PO card or the list via another AJAX call.
            # For now, returning success. JS side can then trigger a refresh of the PO list.
             return JsonResponse({"success": True, "message": "PO items updated.", "po_id": po.id, "refresh_po_list": True})

    except Exception as e:
        logger.error(f"Error in po_edit_items for PO {pk}: {e}", exc_info=True)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({"success": False, "error": str(e)}, status=500)
        # Fallback for non-AJAX if needed
        # messages.error(request, f"Error updating PO items: {e}")

    return redirect('warehouse:warehouse_management') # Fallback redirect

@require_POST
@login_required
@transaction.atomic
def po_delete(request, pk):
    # ... (Implementation from your file) ...
    po = get_object_or_404(PurchaseOrder, pk=pk)

    if not request.user.is_superuser: # Example permission check
        return HttpResponseForbidden("You are not authorized to delete this PO.")

    try:
        po.delete()
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": True, "message": "Purchase Order deleted."})
        # messages.success(request, "Purchase Order deleted.") # For non-AJAX
        return redirect("warehouse:warehouse_management")
    except Exception as e:
        logger.error(f"Error deleting PO {pk}: {e}", exc_info=True)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": str(e)}, status=500)
        # messages.error(request, f"Error deleting PO: {e}")
        return redirect("warehouse:warehouse_management")

def purchase_order_list_partial(request):
    """
    View to handle AJAX requests for filtering and paginating purchase orders.
    Returns an HTML partial containing only the list of POs and pagination.
    Ensures correct context (status_dates, etc.) is passed for modal rendering.
    """
    print("--- purchase_order_list_partial (AJAX refresh v3 - Context Fix) ---")
    purchase_orders_list = PurchaseOrder.objects.select_related('supplier') \
                                           .prefetch_related('items__item__product', 'items__item__warehouse') \
                                           .order_by('-last_updated_date')

    selected_status = request.GET.get('status')
    selected_supplier_id = request.GET.get('supplier')
    query = request.GET.get('q', '').strip()
    page_number = request.GET.get('page', 1)

    print(f"Params: q='{query}', supplier='{selected_supplier_id}', status='{selected_status}', page='{page_number}'")

    # Apply filters
    if selected_status:
        purchase_orders_list = purchase_orders_list.filter(status=selected_status)
    if selected_supplier_id:
        purchase_orders_list = purchase_orders_list.filter(supplier_id=selected_supplier_id)
    if query:
        print(f"Applying query '{query}' to Purchase Orders (including SKU)")
        purchase_orders_list = purchase_orders_list.filter(
            Q(id__icontains=query) |
            Q(supplier__name__icontains=query) |
            Q(items__item__product__name__icontains=query) |
            Q(items__item__product__sku__icontains=query)
        ).distinct()

    # Pagination
    paginator = Paginator(purchase_orders_list, 10)
    try:
        purchase_orders_page = paginator.page(page_number)
    except PageNotAnInteger:
        purchase_orders_page = paginator.page(1)
    except EmptyPage:
        purchase_orders_page = paginator.page(paginator.num_pages)

    print(f"Final PO count for AJAX refresh (page {purchase_orders_page.number}): {len(purchase_orders_page.object_list)}")

    # --- Context Preparation (CRITICAL BLOCK - Copied from handle_purchaseorders_tab) ---
    status_choices = PurchaseOrder.STATUS_CHOICES
    status_dates_for_page = {}
    next_statuses_for_page = {}
    status_date_fields = [f"{code.lower()}_date" for code, _ in status_choices]

    for po in purchase_orders_page:
        po_id_str = str(po.id)
        dates = {}
        for field_name_base in status_date_fields:
            actual_field_name = field_name_base
            if hasattr(po, actual_field_name):
                status_code_key = actual_field_name.replace('_date', '').upper()
                dates[status_code_key] = getattr(po, actual_field_name, None)
        status_dates_for_page[po_id_str] = dates
        next_statuses_for_page[po_id_str] = get_next_status(po.status)
    # --- End of Context Preparation Block ---

    # Add print statement for debugging
    print("DEBUG (AJAX): status_dates_for_page being sent to template:")
    import pprint
    pprint.pprint(status_dates_for_page)

    # --- Final Context ---
    context = {
        'purchase_orders': purchase_orders_page,
        'request': request,
        'status_choices': status_choices,        # Now included
        'status_dates': status_dates_for_page,   # Now included
        'next_statuses': next_statuses_for_page, # Now included
        'selected_status': selected_status,
        'selected_supplier': selected_supplier_id,
        'query': query,
        # 'page_obj': purchase_orders_page # Pass if template uses page_obj for pagination
    }
    # Render the partial template used for AJAX updates
    return render(request, 'warehouse/po_list_items.html', context)


def get_filtered_po_data(request):
    logger.debug(f"get_filtered_po_data request.GET: {request.GET}")

    try: # Wrap the whole view in a try-except to catch unexpected errors and log them
        purchase_orders_qs = PurchaseOrder.objects.select_related('supplier') \
                                           .prefetch_related(
                                               'items__item__product',
                                               'items__item__warehouse',
                                               'items__item__supplier'
                                            ) \
                                           .order_by('-last_updated_date')

        selected_status = request.GET.get('status')
        selected_supplier_id_str = request.GET.get('supplier')
        query = request.GET.get('q', '').strip()
        raw_page_number = request.GET.get('page', '1')

        try:
            page_to_request = int(raw_page_number)
            if page_to_request <= 0: page_to_request = 1
        except ValueError:
            logger.warning(f"Invalid page number '{raw_page_number}' received. Defaulting to 1.")
            page_to_request = 1

        logger.debug(f"Filtering POs with: status='{selected_status}', supplier_id='{selected_supplier_id_str}', q='{query}', page='{page_to_request}'")

        if selected_status:
            purchase_orders_qs = purchase_orders_qs.filter(status=selected_status)
        if selected_supplier_id_str and selected_supplier_id_str.isdigit():
            try:
                purchase_orders_qs = purchase_orders_qs.filter(supplier_id=int(selected_supplier_id_str))
            except ValueError:
                logger.error(f"ValueError converting selected_supplier_id_str '{selected_supplier_id_str}' to int.")
        elif selected_supplier_id_str:
            logger.debug(f"selected_supplier_id '{selected_supplier_id_str}' is present but not a valid digit; supplier filter not applied.")

        if query:
            q_filters = Q(supplier__name__icontains=query) | \
                        Q(items__item__product__name__icontains=query) | \
                        Q(items__item__product__sku__icontains=query)
            if query.isdigit():
                try:
                    q_filters |= Q(id=int(query))
                except ValueError:
                    logger.warning(f"Query '{query}' identified as isdigit but failed int conversion for ID search.")
            purchase_orders_qs = purchase_orders_qs.filter(q_filters).distinct()

        paginator = Paginator(purchase_orders_qs, 10)
        try:
            purchase_orders_page = paginator.page(page_to_request)
        except PageNotAnInteger:
            purchase_orders_page = paginator.page(1)
        except EmptyPage:
            if paginator.num_pages > 0:
                purchase_orders_page = paginator.page(paginator.num_pages)
            else:
                purchase_orders_page = Page([], page_to_request, paginator)

        po_list_serialized = []
        logger.debug(f"Serializing {len(purchase_orders_page.object_list)} POs for page {purchase_orders_page.number}.")
        for po in purchase_orders_page.object_list:
            try:
                items_data = []
                for item_obj in po.items.all():
                    product_sku = "N/A"
                    product_name = "N/A"
                    warehouse_name = "N/A"
                    po_item_id_for_edit = None
                    product_variant_id_for_edit = None

                    if item_obj and item_obj.item:
                        if item_obj.item.product:
                            product_sku = item_obj.item.product.sku
                            product_name = item_obj.item.product.name
                        if hasattr(item_obj.item, 'warehouse') and item_obj.item.warehouse: # Check if warehouse attribute exists
                            warehouse_name = item_obj.item.warehouse.name

                        po_item_id_for_edit = item_obj.id
                        product_variant_id_for_edit = item_obj.item.id
                    else:
                        logger.warning(f"PO {po.id} item (POItem ID: {item_obj.id if item_obj else 'Unknown'}) missing related refs.")

                    items_data.append({
                        'po_item_id': po_item_id_for_edit,
                        'item_id': product_variant_id_for_edit,
                        'sku': product_sku,
                        'name': product_name,
                        'warehouse_name': warehouse_name,
                        'quantity': item_obj.quantity if item_obj else 0,
                        'price': str(item_obj.price) if item_obj and item_obj.price is not None else "0.00",
                        'total_price': str(item_obj.total_price) if item_obj and hasattr(item_obj, 'total_price') and item_obj.total_price is not None else "0.00",
                    })

                po_data = {
                    'id': po.id,
                    'supplier': po.supplier.code if po.supplier else 'N/A',
                    'status': po.status,
                    'status_display': po.get_status_display(),
                    'eta': po.eta.strftime('%Y-%m-%d') if po.eta else None,
                    'last_updated_date': po.last_updated_date.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if po.last_updated_date else None,
                    'total_amount': str(po.total_amount) if hasattr(po, 'total_amount') and po.total_amount is not None else "0.00",
                    'items': items_data,
                }
                po_list_serialized.append(po_data)
            except Exception as e_po: # More specific exception handling for a single PO
                logger.error(f"Error serializing individual PO ID {po.id}: {e_po}", exc_info=True)
                po_list_serialized.append({'id': po.id, 'error': f'Failed to serialize PO: {str(e_po)}'})

        # === MODIFICATION START: Use your helper to get common PO rendering context ===
        # This assumes _get_po_context_for_rendering is defined in this file or imported
        # and that PurchaseOrder.STATUS_CHOICES is accessible.
        common_po_rendering_context = _get_po_context_for_rendering(purchase_orders_page)
        # === MODIFICATION END ===

        context_for_json = {
            'purchase_orders': po_list_serialized,
            'current_user_is_superuser': request.user.is_superuser,
            'page': purchase_orders_page.number,
            'has_next': purchase_orders_page.has_next(),
            'has_prev': purchase_orders_page.has_previous(),
            'next_page_number': purchase_orders_page.next_page_number() if purchase_orders_page.has_next() else None,
            'previous_page_number': purchase_orders_page.previous_page_number() if purchase_orders_page.has_previous() else None,
            'total_pages': paginator.num_pages,
            # Merge the common rendering context (status_choices, status_dates, next_statuses)
            **common_po_rendering_context
        }

        logger.debug(f"Context for JsonResponse: page {context_for_json['page']}, num_pos: {len(context_for_json['purchase_orders'])}, has_status_choices: {'status_choices' in context_for_json}")
        return JsonResponse(context_for_json)

    except Exception as e_view: # Catch-all for any other unexpected error in the view
        logger.error(f"Unhandled error in get_filtered_po_data view: {e_view}", exc_info=True)
        return JsonResponse({'error': 'An unexpected server error occurred.'}, status=500)

# --- Load More POs View ---
def load_more_pos(request):
    logger.debug("--- load_more_pos view ---")
    query = request.GET.get("q", "").strip()
    selected_supplier_id = request.GET.get("supplier")
    selected_status = request.GET.get("status")
    raw_page = request.GET.get('page', '1') # Page to load
    try:
        page = int(raw_page)
        if page <= 0: page = 1
    except ValueError:
        logger.warning(f"Invalid page number '{raw_page}' for load_more_pos. Defaulting to 1.")
        page = 1

    logger.debug(f"Params: query='{query}', supplier='{selected_supplier_id}', status='{selected_status}', page={page}")

    purchase_orders_qs = PurchaseOrder.objects.select_related('supplier') \
                                   .prefetch_related(
                                       'items__item__product',
                                       'items__item__warehouse',
                                       'items__item__supplier'
                                    ) \
                                   .order_by('-last_updated_date')
    if selected_supplier_id:
        purchase_orders_qs = purchase_orders_qs.filter(supplier_id=selected_supplier_id)
    if selected_status:
        purchase_orders_qs = purchase_orders_qs.filter(status=selected_status)
    if query:
        q_filters = Q(supplier__name__icontains=query) | \
                    Q(items__item__product__name__icontains=query) | \
                    Q(items__item__product__sku__icontains=query)
        if query.isdigit():
            q_filters |= Q(id=query)
        purchase_orders_qs = purchase_orders_qs.filter(q_filters).distinct()

    paginator = Paginator(purchase_orders_qs, 10)
    try:
        purchase_orders_page = paginator.page(page)
    except PageNotAnInteger:
        logger.warning(f"PageNotAnInteger for page '{page}' in load_more_pos. Serving page 1.")
        purchase_orders_page = paginator.page(1) # Should not happen if JS sends valid page number
        if not purchase_orders_page.object_list: # If page 1 is also empty
             return HttpResponse("")
    except EmptyPage:
        logger.info(f"EmptyPage for page '{page}' in load_more_pos. No more items.")
        return HttpResponse("") # Return empty response, JS will hide button

    # Prepare context required by _po_list_items.html for modals
    common_po_rendering_context = _get_po_context_for_rendering(purchase_orders_page)

    context = {
        "purchase_orders": purchase_orders_page.object_list, # Pass the list of PO objects for this page
        "page_obj": purchase_orders_page, # Pass the Paginator page object
        "has_next": purchase_orders_page.has_next(), # For the next "load more" button in the partial
        "request": request, # Pass request for template tags like {% csrf_token %} if used by included modals
        **common_po_rendering_context # status_choices, status_dates, next_statuses
    }

    # Render only the items list part
    html = render_to_string("warehouse/_po_list_items.html", context, request=request)
    return HttpResponse(html)

