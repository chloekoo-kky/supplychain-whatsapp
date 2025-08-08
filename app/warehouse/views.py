# ===== warehouse/views.py =====
import pprint
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest, JsonResponse, HttpResponse, HttpResponseForbidden
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.serializers import serialize
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import QuerySet, Q, Sum, Avg
from django.db.models.functions import RowNumber
from itertools import groupby

from datetime import timedelta
import logging
import json



from django.views.decorators.http import require_POST
from django.db import transaction
from django.utils import timezone
from django.contrib import messages
from django.urls import reverse # For generating URLs in JSON response


from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt



from django.template.loader import render_to_string

from .models import (
    WarehouseProduct, Warehouse, PurchaseOrder, PurchaseOrderItem,
    PurchaseOrderStatusLog, PurchaseOrderReceiptItem # New models
)
from inventory.models import Product, Supplier, StockTransaction
from .forms import WarehouseProductDetailForm



DEFAULT_TAB = "warehouseproduct"
logger = logging.getLogger(__name__)

def _get_po_context_for_rendering(purchase_orders_page_obj):
    """Helper to generate context for rendering PO lists and modals."""
    logger.debug("--- Debugging _get_po_context_for_rendering ---")
    status_choices_list = PurchaseOrder.STATUS_CHOICES
    status_dates_dict = {}

    po_list = purchase_orders_page_obj.object_list if hasattr(purchase_orders_page_obj, 'object_list') else purchase_orders_page_obj
    if not po_list:
        logger.debug("po_list is empty. Returning empty context.")
        return {"status_choices": status_choices_list, "status_dates": {}}

    po_ids = [po.id for po in po_list]
    logger.debug(f"Processing PO IDs: {po_ids}")

    logs = PurchaseOrderStatusLog.objects.filter(purchase_order_id__in=po_ids).order_by('purchase_order_id', 'timestamp')
    logger.debug(f"Found {logs.count()} status logs for these POs.")

    for po_id in po_ids:
        status_dates_dict[str(po_id)] = {}

    for log in logs:
        po_id_str = str(log.purchase_order_id)
        if log.status not in status_dates_dict[po_id_str]:
            status_dates_dict[po_id_str][log.status] = log.timestamp
    logger.debug(f"Status dates dictionary after processing logs: {status_dates_dict}")

    for po in po_list:
        po_id_str = str(po.id)
        if po.created_at:
             status_dates_dict[po_id_str]['DRAFT'] = po.created_at
             logger.debug(f"Set DRAFT date for PO-{po_id_str} to {po.created_at}")

    logger.debug(f"Final status_dates context being returned: {status_dates_dict}")
    return {
        "status_choices": status_choices_list,
        "status_dates": status_dates_dict,
    }



# --- Tab Handler for Warehouse Products ---
def handle_warehouseproduct_tab(request):
    """
    Handles both initial load and AJAX filtering for the Warehouse Products tab.
    """
    user = request.user
    selected_warehouse_id = request.GET.get("warehouse")
    selected_supplier_id = request.GET.get("supplier")
    query_param = request.GET.get("q", "").strip()
    page_number = request.GET.get('page', '1')

    # Base queryset
    products_qs = WarehouseProduct.objects.select_related(
        'product', 'warehouse', 'supplier'
        ).prefetch_related(
        'purchaseorderitem_set__purchase_order__supplier'
        )

    # Filter by user permissions
    if not user.is_superuser and user.warehouse:
        products_qs = products_qs.filter(warehouse=user.warehouse)

    # Apply request filters
    if selected_warehouse_id:
        products_qs = products_qs.filter(warehouse_id=selected_warehouse_id)
    if selected_supplier_id:
        products_qs = products_qs.filter(supplier_id=selected_supplier_id)
    if query_param:
        products_qs = products_qs.filter(
            Q(product__name__icontains=query_param) | Q(product__sku__icontains=query_param)
        ).distinct()

    products_qs = products_qs.order_by('product__name')

    # Paginate the results
    paginator = Paginator(products_qs, 20) # You can adjust the page size
    products_page = paginator.get_page(page_number)

    # Determine which suppliers and warehouses to show in the filter panel
    if user.is_superuser:
        warehouses_list = Warehouse.objects.all().order_by('name')
        suppliers_list = Supplier.objects.all().order_by('code')
    else:
        warehouses_list = Warehouse.objects.filter(pk=user.warehouse.id) if user.warehouse else Warehouse.objects.none()
        suppliers_list = Supplier.objects.filter(
            warehouseproduct__warehouse__in=warehouses_list
        ).distinct().order_by('code')

    context = {
        "products": products_page,
        "warehouses": warehouses_list,
        "suppliers": suppliers_list,
        "selected_warehouse_id": selected_warehouse_id,
        "selected_supplier": selected_supplier_id,
        "query": query_param,
        "user": user,
    }
    # The main warehouse_management view will handle rendering this partial for AJAX requests
    return context, "warehouse/warehouse_product_partial.html"

# --- Tab Handler for Purchase Orders ---
def handle_purchaseorders_tab(request):
    """Prepares context for the Purchase Orders tab for the initial page load."""
    logger.debug("--- handle_purchaseorders_tab (Initial Load) ---")
    query = request.GET.get("q", "").strip()
    selected_supplier_id = request.GET.get("supplier")
    selected_status = request.GET.get("status")
    selected_warehouse_id = request.GET.get("warehouse")
    page = request.GET.get('page', '1')
    logger.debug(f"Initial load filters: q={query}, supplier={selected_supplier_id}, status={selected_status}, wh={selected_warehouse_id}")

    purchase_orders_qs = PurchaseOrder.objects.select_related('supplier').prefetch_related(
        'items', 'items__item__product', 'items__item__warehouse'
    ).order_by('-last_updated_date')

    # Apply filters
    if selected_supplier_id:
        purchase_orders_qs = purchase_orders_qs.filter(supplier_id=selected_supplier_id)
    if selected_status:
        purchase_orders_qs = purchase_orders_qs.filter(status=selected_status)
    if selected_warehouse_id:
        purchase_orders_qs = purchase_orders_qs.filter(items__item__warehouse_id=selected_warehouse_id).distinct()
    if query:
        q_filters = Q(supplier__name__icontains=query) | Q(items__item__product__name__icontains=query) | Q(id__icontains=query)
        purchase_orders_qs = purchase_orders_qs.filter(q_filters).distinct()

    paginator = Paginator(purchase_orders_qs, 10)
    try:
        purchase_orders_page = paginator.page(page)
    except (PageNotAnInteger, EmptyPage):
        purchase_orders_page = paginator.page(1)

    for po in purchase_orders_page:
        logger.debug(f"Initial load: PO-{po.id} has {po.items.count()} item(s).")

    common_po_rendering_context = _get_po_context_for_rendering(purchase_orders_page)

    context = {
        "purchase_orders": purchase_orders_page,
        "page_obj": purchase_orders_page,
        "warehouses": Warehouse.objects.all().order_by('name'),
        "suppliers": Supplier.objects.all().order_by('code'),
        "selected_warehouse": selected_warehouse_id,
        "selected_supplier": selected_supplier_id,
        "selected_status": selected_status,
        "query": query,
        **common_po_rendering_context
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
    """
    Handles AJAX search/filter requests for the Warehouse Products tab.
    Now returns a JSON object with HTML and the filtered count.
    """
    query = request.GET.get('q', '').strip()
    warehouse_id_param = request.GET.get('warehouse')
    supplier_id = request.GET.get('supplier')
    user = request.user

    products_qs = WarehouseProduct.objects.select_related('product', 'warehouse', 'supplier')

    # Apply filters based on user role and request params
    if not user.is_superuser and user.warehouse:
        products_qs = products_qs.filter(warehouse=user.warehouse)
    elif warehouse_id_param:
        products_qs = products_qs.filter(warehouse_id=warehouse_id_param)

    if supplier_id:
        products_qs = products_qs.filter(supplier_id=supplier_id)

    if query:
        q_object = Q(product__name__icontains=query) | Q(product__sku__icontains=query)
        products_qs = products_qs.filter(q_object).distinct()

    # Get the total count *after* filtering
    product_count = products_qs.count()

    products_qs = products_qs.order_by('product__name')

    # Paginate the results
    paginator = Paginator(products_qs, 20) # Or your desired page size
    page_number = request.GET.get('page', 1)
    products_page = paginator.get_page(page_number)

    context = {
        "products": products_page,
        "user": user,
        "today": timezone.now().date(),
    }

    # Render the table rows partial to an HTML string
    html = render_to_string("warehouse/_search_results.html", context)

    # Return everything in a single JSON response
    return JsonResponse({"html": html, "count": product_count})

@login_required
def warehouse_product_list_partial(request):
    """
    This view handles AJAX requests for the filtered product list.
    It returns a JSON response containing the rendered HTML for the table rows
    and the total count of filtered products.
    """
    # This logic is moved from your old 'search' view and handle_warehouseproduct_tab view
    user = request.user
    selected_warehouse_id = request.GET.get("warehouse")
    selected_supplier_id = request.GET.get("supplier")
    query_param = request.GET.get("q", "").strip()
    page_number = request.GET.get('page', 1)

    products_qs = WarehouseProduct.objects.select_related('product', 'warehouse', 'supplier')

    if not user.is_superuser and user.warehouse:
        products_qs = products_qs.filter(warehouse=user.warehouse)

    if selected_warehouse_id:
        products_qs = products_qs.filter(warehouse_id=selected_warehouse_id)
    if selected_supplier_id:
        products_qs = products_qs.filter(supplier_id=selected_supplier_id)
    if query_param:
        products_qs = products_qs.filter(
            Q(product__name__icontains=query_param) | Q(product__sku__icontains=query_param)
        ).distinct()

    # Get the total count *after* filtering
    product_count = products_qs.count()
    products_qs = products_qs.order_by('product__name')

    paginator = Paginator(products_qs, 20)
    products_page = paginator.get_page(page_number)

    context = {
        "products": products_page,
        "user": user,
        "today": timezone.now().date(),
    }

    # Render just the table rows to an HTML string
    html_rows = render_to_string("warehouse/_warehouse_product_rows.html", context, request=request)
    html_modals = render_to_string("warehouse/_warehouse_product_modals.html", context, request=request)

    return JsonResponse({
            "html_rows": html_rows,
            "html_modals": html_modals,
            "count": product_count
        })

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
@transaction.atomic
def confirm_create_po(request):
    logger.debug("--- confirm_create_po view ---")
    try:
        data = json.loads(request.body)
        orders_data = data.get("orders", {})
        logger.debug(f"Received PO creation data: {orders_data}")

        if not orders_data:
            return JsonResponse({"success": False, "error": "No orders data provided."}, status=400)

        created_po_ids = []
        for supplier_id_str, items in orders_data.items():
            supplier_id = int(supplier_id_str)
            supplier = Supplier.objects.get(pk=supplier_id)

            po = PurchaseOrder(supplier=supplier, status='DRAFT')
            po.save()
            logger.debug(f"Created new PurchaseOrder with ID: {po.id}")
            created_po_ids.append(po.id)

            PurchaseOrderStatusLog.objects.create(purchase_order=po, status='DRAFT', user=request.user)
            logger.debug(f"Created DRAFT status log for PO-{po.id}")

            for item_data in items:
                wp_id = int(item_data["warehouse_product_id"])
                wp = WarehouseProduct.objects.get(id=wp_id)
                quantity = int(item_data["quantity"])
                price = wp.product.price

                PurchaseOrderItem.objects.create(
                    purchase_order=po, item=wp, quantity=quantity, price=price
                )
                logger.debug(f"Created PO Item for PO-{po.id}: {quantity}x {wp.product.name}")

        logger.debug(f"PO creation successful. Committing transaction.")
        return JsonResponse({ "success": True, "created_po_ids": created_po_ids })

    except Exception as e:
        logger.error(f"Error in confirm_create_po: {str(e)}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@require_POST
@login_required # Ensure user is logged in
@transaction.atomic # Ensure atomicity
def po_update(request, pk):
    """
    Updates a PO's ETA and status, creating log entries for all status changes, including backfilling.
    """
    po = get_object_or_404(PurchaseOrder, pk=pk)
    new_status = request.POST.get('selected_status')
    eta_str = request.POST.get('eta')

    po.eta = eta_str if eta_str else None

    if new_status and new_status != po.status:
        ordered_statuses = [code for code, _ in PurchaseOrder.STATUS_CHOICES]
        try:
            current_index = ordered_statuses.index(po.status)
            new_index = ordered_statuses.index(new_status)
        except ValueError:
            return JsonResponse({"success": False, "message": f"Invalid status '{new_status}' provided."}, status=400)

        if new_index > current_index:
            # Backfill statuses by creating log entries
            for i in range(current_index, new_index + 1):
                status_to_log = ordered_statuses[i]
                # Create log entry only if one for this status doesn't already exist
                PurchaseOrderStatusLog.objects.get_or_create(
                    purchase_order=po,
                    status=status_to_log,
                    defaults={'user': request.user}
                )

        po.status = new_status

    po.save()
    return JsonResponse({"success": True, "message": "Purchase Order updated successfully.", "refresh_po_list": True})


# --- New View: Get PO Items for Receiving Modal ---
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

@login_required
@require_POST
@transaction.atomic
def process_po_receipt(request, po_id):
    """
    Processes the receipt of items for a PO, updates inventory, and correctly
    sets the final PO status to DELIVERED if all items are now received.
    """
    purchase_order = get_object_or_404(PurchaseOrder.objects.prefetch_related('items'), pk=po_id)

    if purchase_order.status not in ['PAYMENT_MADE', 'PARTIALLY_DELIVERED', 'DELIVERED']:
        return JsonResponse({'success': False, 'message': f'Cannot process receipt for PO in status {purchase_order.get_status_display()}.'}, status=400)

    try:
        data = json.loads(request.body)
        received_items_info = data.get('items', [])
        receipt_notes = data.get('notes', '')

        if not any(int(item.get('quantity_received_now', 0)) > 0 for item in received_items_info):
            return JsonResponse({'success': True, 'message': 'No items were marked as received.'})

        # --- Step 1: Process all item updates first ---
        updated_po_items = []
        for item_info in received_items_info:
            quantity_received_now = int(item_info.get('quantity_received_now', 0))
            if quantity_received_now <= 0:
                continue

            po_item = get_object_or_404(PurchaseOrderItem.objects.select_related('item__product', 'item__warehouse'),
                                        pk=item_info.get('po_item_id'), purchase_order=purchase_order)

            if quantity_received_now > po_item.balance_quantity:
                raise ValueError(f"Cannot receive {quantity_received_now} for {po_item.item.product.name}. Max balance is {po_item.balance_quantity}.")

            # Update received quantity and inventory
            po_item.received_quantity += quantity_received_now
            po_item.save()

            warehouse_product = po_item.item
            warehouse_product.quantity += quantity_received_now
            warehouse_product.save()

            updated_po_items.append({'item': po_item, 'qty_now': quantity_received_now})

            # Create a corresponding stock transaction record
            StockTransaction.objects.create(
                warehouse=warehouse_product.warehouse,
                warehouse_product=warehouse_product,
                product=warehouse_product.product,
                transaction_type='IN',
                quantity=quantity_received_now,
                reference_note=f"Receipt for PO-{purchase_order.id}", # Simplified note
                related_po=purchase_order
            )

        # --- Step 2: Determine the final status AFTER all items are updated ---
        # We need to refresh the PO from the database to get the latest state of its items
        purchase_order.refresh_from_db()
        new_status = 'DELIVERED' if purchase_order.is_fully_received else 'PARTIALLY_DELIVERED'

        # --- Step 3: Create a single log entry for this receipt event ---
        status_log = PurchaseOrderStatusLog.objects.create(
            purchase_order=purchase_order,
            status=new_status,
            notes=receipt_notes,
            user=request.user
        )

        # --- Step 4: Link the received items to the log entry ---
        for received_item_data in updated_po_items:
            PurchaseOrderReceiptItem.objects.create(
                status_log=status_log,
                po_item=received_item_data['item'],
                quantity_received_this_time=received_item_data['qty_now']
            )

        # --- Step 5: Update the main PO status and save ---
        purchase_order.status = new_status
        purchase_order.save()

        return JsonResponse({
            'success': True,
            'message': 'Items received and inventory updated successfully.',
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

def purchase_order_table(request):
    """
    View to handle AJAX requests for filtering and paginating purchase orders.
    Returns an HTML partial containing only the list of POs and pagination.
    """
    try:
        # Start with the base queryset
        purchase_orders_list = PurchaseOrder.objects.select_related('supplier') \
                                               .prefetch_related('items__item__product', 'items__item__warehouse') \
                                               .order_by('-last_updated_date')

        # Get filter parameters from the request
        selected_status = request.GET.get('status')
        selected_supplier_id = request.GET.get('supplier')
        warehouse_id = request.GET.get('warehouse')
        query = request.GET.get('q', '').strip()
        page_number = request.GET.get('page', 1)

        # Apply filters to the queryset
        if selected_status:
            purchase_orders_list = purchase_orders_list.filter(status=selected_status)
        if selected_supplier_id:
            purchase_orders_list = purchase_orders_list.filter(supplier_id=selected_supplier_id)
        if warehouse_id:
            # CORRECTED: Use the correct variable 'purchase_orders_list' and the correct DB lookup path
            purchase_orders_list = purchase_orders_list.filter(items__item__warehouse_id=warehouse_id).distinct()
        if query:
            purchase_orders_list = purchase_orders_list.filter(
                Q(id__icontains=query) |
                Q(supplier__name__icontains=query) |
                Q(items__item__product__name__icontains=query) |
                Q(items__item__product__sku__icontains=query)
            ).distinct()

        # Paginate the filtered queryset
        paginator = Paginator(purchase_orders_list, 10)
        try:
            purchase_orders_page = paginator.page(page_number)
        except (PageNotAnInteger, EmptyPage):
            purchase_orders_page = paginator.page(1)

        # CORRECTED: Use the working helper function to get context needed for the modals
        common_context = _get_po_context_for_rendering(purchase_orders_page)

        context = {
            'purchase_orders': purchase_orders_page,
            'request': request,
            'page_obj': purchase_orders_page,
            **common_context
        }

        return render(request, 'warehouse/_po_table_with_pagination.html', context)

    except Exception as e:
        # ADDED: Graceful error handling to prevent 500 crashes
        logger.error(f"Error in purchase_order_table: {e}", exc_info=True)
        # Return a simple text error response instead of crashing
        return HttpResponse(f"An error occurred while processing your request. Please check the server logs. Error: {e}", status=500)


def get_filtered_po_data(request):
    """View to handle AJAX requests for filtering and paginating purchase orders."""
    logger.debug(f"get_filtered_po_data request.GET: {request.GET}")

    purchase_orders_qs = PurchaseOrder.objects.select_related('supplier').prefetch_related(
        'items__item__product', 'items__item__warehouse'
    ).order_by('-last_updated_date')

    # Get filter parameters from request
    selected_status = request.GET.get('status')
    selected_supplier_id_str = request.GET.get('supplier')
    selected_warehouse_id_str = request.GET.get('warehouse') # <-- ADDED
    query = request.GET.get('q', '').strip()
    page_to_request = request.GET.get('page', '1')

    # Apply filters
    if selected_status:
        purchase_orders_qs = purchase_orders_qs.filter(status=selected_status)
    if selected_supplier_id_str:
        purchase_orders_qs = purchase_orders_qs.filter(supplier_id=selected_supplier_id_str)
    if selected_warehouse_id_str: # <-- ADDED
        purchase_orders_qs = purchase_orders_qs.filter(items__item__warehouse_id=selected_warehouse_id_str).distinct()
    if query:
        q_filters = Q(supplier__name__icontains=query) | \
                    Q(items__item__product__name__icontains=query) | \
                    Q(items__item__product__sku__icontains=query)
        if query.isdigit():
            q_filters |= Q(id=query)
        purchase_orders_qs = purchase_orders_qs.filter(q_filters).distinct()

    # Pagination
    paginator = Paginator(purchase_orders_qs, 10)
    try:
        purchase_orders_page = paginator.page(page_to_request)
    except (PageNotAnInteger, EmptyPage):
        purchase_orders_page = paginator.page(1)

    # Serialize PO data
    po_list_serialized = []
    for po in purchase_orders_page.object_list:
        items_data = []
        for item_obj in po.items.all():
            if item_obj and item_obj.item:
                items_data.append({
                    'po_item_id': item_obj.id,
                    'item_id': item_obj.item.id,
                    'sku': item_obj.item.product.sku,
                    'name': item_obj.item.product.name,
                    'warehouse_name': item_obj.item.warehouse.name if item_obj.item.warehouse else 'N/A', # <-- ENSURED
                    'quantity': item_obj.quantity,
                    'price': str(item_obj.price),
                    'total_price': str(item_obj.total_price),
                })
        po_list_serialized.append({
            'id': po.id,
            'supplier_name': po.supplier.name if po.supplier else 'N/A',
            'status': po.status,
            'status_display': po.get_status_display(),
            'eta': po.eta,
            'last_updated_date': po.last_updated_date,
            'total_amount': str(po.total_amount),
            'items': items_data,
        })

    common_po_rendering_context = _get_po_context_for_rendering(purchase_orders_page)

    context_for_json = {
        'purchase_orders': po_list_serialized,
        'current_user_is_superuser': request.user.is_superuser,
        'page': purchase_orders_page.number,
        'has_next': purchase_orders_page.has_next(),
        'total_pages': paginator.num_pages,
        **common_po_rendering_context
    }
    return JsonResponse(context_for_json, encoder=DjangoJSONEncoder)


@login_required
def load_more_pos(request):
    """
    Handles AJAX requests for the "Explore More" button on the PO list.
    Renders only the new table rows (<tr>...</tr>) to be appended by the frontend.
    """
    logger.debug("--- Executing load_more_pos view ---")

    # 1. Get all filter and pagination parameters from the request
    query = request.GET.get("q", "").strip()
    selected_supplier_id = request.GET.get("supplier")
    selected_status = request.GET.get("status")
    selected_warehouse_id = request.GET.get("warehouse") # <-- Added missing parameter
    page = request.GET.get('page', '1')

    # 2. Build the base queryset, same as in the main list view
    purchase_orders_qs = PurchaseOrder.objects.select_related('supplier').prefetch_related(
        'items__item__product',
        'items__item__warehouse'
    ).order_by('-last_updated_date')

    # 3. Apply all relevant filters to the queryset
    if selected_supplier_id:
        purchase_orders_qs = purchase_orders_qs.filter(supplier_id=selected_supplier_id)
    if selected_status:
        purchase_orders_qs = purchase_orders_qs.filter(status=selected_status)
    if selected_warehouse_id:
        purchase_orders_qs = purchase_orders_qs.filter(items__item__warehouse_id=selected_warehouse_id).distinct()
    if query:
        q_filters = Q(supplier__name__icontains=query) | \
                    Q(items__item__product__name__icontains=query) | \
                    Q(items__item__product__sku__icontains=query)
        if query.isdigit():
            q_filters |= Q(id=query)
        purchase_orders_qs = purchase_orders_qs.filter(q_filters).distinct()

    # 4. Paginate the filtered results
    paginator = Paginator(purchase_orders_qs, 10)
    try:
        purchase_orders_page = paginator.page(page)
    except (PageNotAnInteger, EmptyPage):
        # This means the requested page has no results.
        # Return an empty HTTP response. The JavaScript will see this and hide the "Explore More" button.
        return HttpResponse("")

    if not purchase_orders_page.object_list:
        return JsonResponse({"html_rows": "", "html_modals": "", "has_next": False})

    # Prepare context for rendering templates
    context = {
        "purchase_orders": purchase_orders_page.object_list,
        "request": request,
        "status_choices": PurchaseOrder.STATUS_CHOICES,
        "status_dates": _get_po_context_for_rendering(purchase_orders_page).get("status_dates"),
    }

    # Render the two partials to strings
    html_rows = render_to_string("warehouse/_po_table_rows_only.html", context)
    html_modals = render_to_string("warehouse/_po_modals_only.html", context)

    # Return the data as a JSON object
    return JsonResponse({
        "html_rows": html_rows,
        "html_modals": html_modals,
        "has_next": purchase_orders_page.has_next(),
        "next_page_number": purchase_orders_page.next_page_number() if purchase_orders_page.has_next() else None,
    })


@login_required
def product_stats_json(request, wp_id):
    """
    Handles AJAX requests to fetch stock transactions and PO stats for a
    single WarehouseProduct.
    """
    try:
        product = get_object_or_404(WarehouseProduct.objects.select_related('product'), pk=wp_id)

        # 1. Get recent stock transactions
        # CORRECTED: Use 'transaction_date' instead of 'created_at'
        transactions = StockTransaction.objects.filter(warehouse_product=product).order_by('-transaction_date')[:20]
        transactions_data = [{
            'date': tx.transaction_date.strftime('%Y-%m-%d %H:%M'), # CORRECTED
            'type_code': tx.transaction_type,
            'type_display': tx.get_transaction_type_display(),
            'quantity': tx.quantity,
            'reference': tx.reference_note
        } for tx in transactions]

        # 2. Get purchase order history for this specific product (This part was already correct)
        po_items = PurchaseOrderItem.objects.filter(item=product).select_related('purchase_order__supplier').order_by('-purchase_order__created_at')[:10]
        po_history_data = [{
            'po_id': item.purchase_order.id,
            'date': item.purchase_order.created_at.strftime('%Y-%m-%d'),
            'quantity': item.quantity,
            'price': f"{item.price:.2f}"
        } for item in po_items]

        # 3. Calculate statistics
        thirty_days_ago = timezone.now() - timedelta(days=30)
        sales_last_30_days = StockTransaction.objects.filter(
            warehouse_product=product,
            transaction_type='OUT',
            transaction_date__gte=thirty_days_ago
        ).aggregate(total_sold=Sum('quantity'))['total_sold'] or 0

        # --- NEW CALCULATION LOGIC ---
        existing_stock = product.quantity
        incoming_stock = product.pending_arrival  # Uses the new model property
        total_stock = existing_stock + incoming_stock

        stock_lasts_for_months = 0
        if sales_last_30_days > 0:
            # This calculation gives a result in months
            stock_lasts_for_months = total_stock / sales_last_30_days
        # --- END OF NEW LOGIC ---

        stats_data = {
            'sales_last_30_days': sales_last_30_days,
            'monthly_avg_sales': sales_last_30_days,
            'recommended_po_qty': (sales_last_30_days * 1.5),
            'total_stock': total_stock, # Pass total stock for display
            'stock_lasts_for_months': stock_lasts_for_months, # Pass the new stat
        }

        response = {
            'success': True,
            'product_name': product.product.name,
            'transactions': transactions_data,
            'po_history': po_history_data,
            'statistics': stats_data,
        }
        return JsonResponse(response)

    except WarehouseProduct.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Product not found.'}, status=404)
    except Exception as e:
        logger.error(f"Error in product_stats_json for wp_id {wp_id}: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': 'An unexpected server error occurred.'}, status=500)


@login_required
def manage_product_list(request):
    """
    Handles both regular page loads and AJAX requests for dynamic searching.
    For superusers, it also calculates the average unit price from the last 3 POs.
    """

    user = request.user
    product_list = WarehouseProduct.objects.select_related('product', 'warehouse').order_by('product__name')

    # --- START: WAREHOUSE FILTERING LOGIC ---
    selected_warehouse_id = request.GET.get('warehouse')

    if not user.is_superuser and user.warehouse:
        # Non-superusers are always locked to their assigned warehouse.
        product_list = product_list.filter(warehouse=user.warehouse)
    elif user.is_superuser and selected_warehouse_id:
        # Superusers can filter by selecting a warehouse.
        product_list = product_list.filter(warehouse_id=selected_warehouse_id)
    elif not user.is_superuser and not user.warehouse:
        # If a non-superuser has no warehouse, they see an empty list.
        product_list = product_list.none()
    # --- END: WAREHOUSE FILTERING LOGIC ---


    search_query = request.GET.get('q', '')
    if search_query:
        product_list = product_list.filter(
            Q(product__name__icontains=search_query) |
            Q(product__sku__icontains=search_query)
        )

    paginator = Paginator(product_list, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    if request.user.is_superuser and page_obj.object_list:
        warehouse_products_on_page = list(page_obj.object_list)
        product_ids = [wp.product.id for wp in warehouse_products_on_page]

        # ++ MODIFIED: Added 'price__gt=0' to exclude zero-price PO items from the calculation ++
        po_items = PurchaseOrderItem.objects.filter(
            item__product_id__in=product_ids,
            price__gt=0  # Exclude items where price is null OR zero
        ).order_by(
            'item__product_id',
            '-purchase_order__created_at'
        ).values(
            'item__product_id',
            'price'
        )

        po_items_by_product = {
            key: list(group)
            for key, group in groupby(po_items, key=lambda x: x['item__product_id'])
        }

        for wp in warehouse_products_on_page:
            product_po_items = po_items_by_product.get(wp.product.id, [])
            latest_items = product_po_items[:3]
            if latest_items:
                total_price = sum(item['price'] for item in latest_items)
                count = len(latest_items)
                wp.avg_po_price = total_price / count if count > 0 else None
            else:
                wp.avg_po_price = None

        page_obj.object_list = warehouse_products_on_page

    warehouses_for_filter = Warehouse.objects.all().order_by('name')

    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'title': "Manage Product Details",
        'warehouses': warehouses_for_filter,
        'selected_warehouse': int(selected_warehouse_id) if selected_warehouse_id else None,

    }

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'warehouse/_manage_product_list_partial.html', context)

    return render(request, 'warehouse/manage_product_list.html', context)


@login_required
def manage_product_details(request, wp_id):
    """
    Handles both AJAX and regular requests for editing WarehouseProduct details.
    """
    warehouse_product = get_object_or_404(WarehouseProduct.objects.select_related('product', 'warehouse'), pk=wp_id)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    avg_po_price = None
    if request.user.is_superuser:
        latest_po_items = PurchaseOrderItem.objects.filter(
            item__product_id=warehouse_product.product.id,
            price__gt=0
        ).order_by('-purchase_order__created_at')[:3]

        if latest_po_items:
            total_price = sum(item.price for item in latest_po_items)
            count = len(latest_po_items)
            avg_po_price = total_price / count if count > 0 else None

    if request.method == 'POST':
        form = WarehouseProductDetailForm(request.POST, request.FILES, instance=warehouse_product)
        if form.is_valid():
            # ++ MODIFIED: Saving the form now automatically saves the new selling_price field ++
            form.save()

            if is_ajax:
                return JsonResponse({'success': True})
            else:
                messages.success(request, f"Successfully updated details for {warehouse_product.product.name}.")
                return redirect('warehouse:manage_product_list')
        else:
            if is_ajax:
                context = {'form': form, 'warehouse_product': warehouse_product, 'avg_po_price': avg_po_price}
                form_html = render_to_string('warehouse/_manage_product_details_form.html', context, request=request)
                return JsonResponse({'success': False, 'form_html': form_html})
            else:
                messages.error(request, "Please correct the errors below.")
    else:
        form = WarehouseProductDetailForm(instance=warehouse_product)

    context = {
        'form': form,
        'warehouse_product': warehouse_product,
        'avg_po_price': avg_po_price,
        'title': f"Manage {warehouse_product.product.name}",
    }

    if is_ajax:
        return render(request, 'warehouse/_manage_product_details_form.html', context)
    else:
        return render(request, 'warehouse/manage_product_details.html', context)


def export_price_list(request):
    """
    Generates a printable HTML page with the selected products for a price list.
    """
    product_ids_str = request.GET.get('ids', '')
    if product_ids_str:
        product_ids = [int(id) for id in product_ids_str.split(',')]
        products = WarehouseProduct.objects.filter(id__in=product_ids).select_related('product')
    else:
        products = WarehouseProduct.objects.none()

    context = {
        'products': products,
        'title': 'Product Price List',
    }
    return render(request, 'warehouse/price_list_export.html', context)
