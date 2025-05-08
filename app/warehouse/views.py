# ===== warehouse/views.py =====
from django.shortcuts import render, redirect, get_object_or_404

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger



from django.db import transaction
from django.db.models import Q, F
from django.db.models.functions import Coalesce

from django.template.loader import render_to_string
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden

from django.utils import timezone


from warehouse.models import WarehouseProduct, Warehouse, PurchaseOrder, PurchaseOrderItem
from inventory.models import Product, Supplier, StockTransaction

from .utils import get_next_status


import json

DEFAULT_TAB = "warehouseproduct"


def handle_warehouseproduct_tab(request):
    user = request.user
    selected_warehouse_id = request.GET.get("warehouse") # URL key is 'warehouse'
    selected_supplier_id = request.GET.get("supplier")   # URL key is 'supplier'
    query_param = request.GET.get("q", "").strip()       # URL key is 'q'

    print(f"--- handle_warehouseproduct_tab ---")
    print(f"Params: warehouse='{selected_warehouse_id}', supplier='{selected_supplier_id}', q='{query_param}'")

    if user.is_superuser:
        products_qs = WarehouseProduct.objects.select_related('product', 'warehouse', 'supplier').all().order_by('product__name')

        if selected_warehouse_id:
            products_qs = products_qs.filter(warehouse_id=selected_warehouse_id)

        if selected_supplier_id:
            # Assuming WarehouseProduct has a direct ForeignKey to Supplier model named 'supplier'
            products_qs = products_qs.filter(supplier_id=selected_supplier_id)

        if query_param:
            print(f"Applying query_param '{query_param}' in handle_warehouseproduct_tab for superuser")
            products_qs = products_qs.filter(
                Q(product__name__icontains=query_param) |
                Q(product__sku__icontains=query_param) |
                Q(warehouse__name__icontains=query_param) # Consider if warehouse name search is desired here
            ).distinct()

        warehouses_list = Warehouse.objects.all()
        suppliers_list = Supplier.objects.all()
    else:
        # Non-superuser logic
        products_qs = WarehouseProduct.objects.select_related('product', 'warehouse', 'supplier').filter(warehouse=user.warehouse).order_by('product__name')
        # Non-superusers might only have supplier filter if applicable, and query
        if selected_supplier_id: # If non-superusers can filter by supplier
             products_qs = products_qs.filter(supplier_id=selected_supplier_id)

        if query_param:
            print(f"Applying query_param '{query_param}' in handle_warehouseproduct_tab for non-superuser")
            products_qs = products_qs.filter(
                Q(product__name__icontains=query_param) |
                Q(product__sku__icontains=query_param)
                # For non-superusers, warehouse name search might not be relevant if fixed to their warehouse
            ).distinct()

        warehouses_list = None # Or Warehouse.objects.filter(pk=user.warehouse_id) if they need to see their own
        # Suppliers relevant to the user's warehouse products
        # This could be complex if products can have different suppliers.
        # A simpler approach might be all suppliers, or suppliers linked to products in their warehouse.
        suppliers_list = Supplier.objects.filter(warehouseproduct__warehouse=user.warehouse).distinct().order_by('name')


    print(f"Final product count for initial tab load: {products_qs.count()}")
    if products_qs.exists():
        print("Sample products for initial tab load (first 3):")
        for p_item in products_qs[:3]:
            print(f"  - Product: {p_item.product.name}, SKU: {p_item.product.sku}, Warehouse: {p_item.warehouse.name}")


    context = {
        "products": products_qs,
        "warehouses": warehouses_list,
        "suppliers": suppliers_list,
        "selected_warehouse_id": selected_warehouse_id, # For template button highlighting
        "selected_supplier": selected_supplier_id,     # For template button highlighting (use 'selected_supplier' if template expects that)
        "query": query_param,                          # Pass query back for search input value
    }
    return context, "warehouse/warehouse_product_partial.html"

def handle_purchaseorders_tab(request):
    print("--- handle_purchaseorders_tab ---")
    query = request.GET.get("q", "").strip()
    selected_supplier_id = request.GET.get("supplier")
    selected_status = request.GET.get("status")
    page_number = request.GET.get("page", 1)

    print(f"Params: q='{query}', supplier='{selected_supplier_id}', status='{selected_status}', page='{page_number}'")

    purchase_orders_qs = PurchaseOrder.objects.select_related('supplier') \
                                           .prefetch_related('items__item__product', 'items__item__warehouse') \
                                           .order_by('-last_updated_date') # Or '-id'

    if selected_supplier_id:
        purchase_orders_qs = purchase_orders_qs.filter(supplier_id=selected_supplier_id)
    if selected_status:
        purchase_orders_qs = purchase_orders_qs.filter(status=selected_status)

    if query:
        print(f"Applying query '{query}' to Purchase Orders")
        purchase_orders_qs = purchase_orders_qs.filter(
            Q(id__icontains=query) |  # Assuming query could be a PO ID
            Q(supplier__name__icontains=query) |
            Q(items__item__product__name__icontains=query) |
            Q(items__item__product__sku__icontains=query)
        ).distinct()

    paginator = Paginator(purchase_orders_qs, 10) # Show 10 POs per page
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    print(f"Final PO count for initial tab load: {page_obj.object_list.count() if hasattr(page_obj, 'object_list') else 0}")

    status_choices = PurchaseOrder.STATUS_CHOICES

    # Prepare status dates and next statuses for the items on the current page
    status_dates_for_page = {}
    next_statuses_for_page = {}
    # Ensure all status date fields exist on your PurchaseOrder model
    # (e.g., draft_date, waiting_invoice_date, etc.)
    status_date_fields = [f"{code.lower()}_date" for code, _ in status_choices]

    for po in page_obj:
        dates = {}
        for field_name_base in status_date_fields:
            # Construct the field name and check if it exists
            # This is a bit safer than just iterating status_choices if model fields differ
            actual_field_name = field_name_base # e.g. draft_date
            if hasattr(po, actual_field_name):
                 # Get the status code part for the key, e.g., 'DRAFT' from 'draft_date'
                status_code_key = actual_field_name.replace('_date','').upper()
                dates[status_code_key] = getattr(po, actual_field_name, None)

        status_dates_for_page[po.id] = dates
        next_statuses_for_page[po.id] = get_next_status(po.status)


    context = {
        "purchase_orders": page_obj,
        "suppliers": Supplier.objects.all().order_by('name'),
        "selected_supplier": selected_supplier_id,
        "selected_status": selected_status,
        "status_choices": status_choices,
        "status_dates": status_dates_for_page, # Pass the correctly prepared dict
        "next_statuses": next_statuses_for_page, # Pass the correctly prepared dict
        "query": query,
        "page_obj": page_obj, # For pagination template
        "request": request, # Useful for some template tags or pagination
    }
    return context, "warehouse/purchase_orders_partial.html"

TAB_HANDLERS = {
    "warehouseproduct": handle_warehouseproduct_tab,
    "purchaseorders": handle_purchaseorders_tab, # Make sure handle_purchaseorders_tab is defined
}

@login_required
def warehouse_management(request):
    tab = request.GET.get("tab", DEFAULT_TAB) # Use DEFAULT_TAB constant

    handler = TAB_HANDLERS.get(tab, handle_warehouseproduct_tab) # Default to warehouse if tab is unknown

    print(f"--- warehouse_management view ---")
    print(f"Request GET: {request.GET}")
    print(f"Selected tab: {tab}, Handler: {handler.__name__ if handler else 'None'}")

    context, partial_template = handler(request)
    context["active_tab"] = tab

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        # This path is taken when JS fetches tab content (e.g., on tab switch or initial load by JS)
        print(f"Rendering partial: {partial_template} for AJAX request")
        return render(request, partial_template, context)

    # This path is taken for a full page load (e.g. user types URL, or F5 refresh)
    print(f"Rendering full page: warehouse/warehouse_management.html, active_tab: {tab}")
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
    warehouse_id = request.GET.get('warehouse')
    supplier_id = request.GET.get('supplier')

    print(f"Received parameters: q='{query}', warehouse_id='{warehouse_id}', supplier_id='{supplier_id}'")

    products_qs = WarehouseProduct.objects.select_related('product', 'warehouse', 'supplier').all()
    print(f"Initial queryset count: {products_qs.count()}")

    if warehouse_id:
        products_qs = products_qs.filter(warehouse_id=warehouse_id)
        print(f"After warehouse_id filter ('{warehouse_id}'), count: {products_qs.count()}")
    if supplier_id:
        products_qs = products_qs.filter(supplier_id=supplier_id)
        print(f"After supplier_id filter ('{supplier_id}'), count: {products_qs.count()}")
    if query:
        print(f"Applying query: '{query}'")
        q_filter = Q(product__name__icontains=query) | \
                   Q(product__sku__icontains=query)
        # Optional: Decide if warehouse name search is desired for AJAX search too
        # q_filter_warehouse_name = Q(warehouse__name__icontains=query)
        # products_qs = products_qs.filter(q_filter | q_filter_warehouse_name).distinct()
        products_qs = products_qs.filter(q_filter).distinct() # Example: Only SKU and Name for AJAX

        print(f"After query filter ('{query}'), count: {products_qs.count()}")
        if products_qs.exists():
            print("Sample results from AJAX search (first 3):")
            for p_item in products_qs[:3]:
                print(f"  - Product: {p_item.product.name}, SKU: {p_item.product.sku}")

    context = {"products": products_qs, "query": query}
    # This AJAX view should only return the list of items, not the whole partial with filters
    return render(request, "warehouse/_search_results.html", context)


@login_required
def warehouseproduct_details(request, pk):
    try:
        wp = WarehouseProduct.objects.get(pk=pk)
        data = {
            "product": str(wp.product),
            "warehouse": str(wp.warehouse),
            "batch_number": wp.batch_number or "N/A",
            "expiry_date": wp.expiry_date.strftime('%Y-%m-%d') if wp.expiry_date else "N/A",
            "quantity": wp.quantity,
            "threshold": wp.threshold,
        }
        return JsonResponse(data)
    except WarehouseProduct.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)


@csrf_exempt
@login_required
@require_POST
def prepare_po_from_selection(request):
    try:
        data = json.loads(request.body)
        selected_items = data.get("selected_items", [])
        healthy_multiplier = float(data.get("healthy_multiplier", 1.5))

        if not selected_items:
            return JsonResponse({"success": False, "message": "No products selected."})

        result = {}

        for wp_id in selected_items:
            try:
                wp = WarehouseProduct.objects.select_related('product', 'warehouse').get(id=wp_id)
                supplier = wp.supplier or wp.product.supplier

                if not supplier:
                    continue  # 跳过没有供应商的产品

                if supplier.id not in result:
                    result[supplier.id] = {
                        "supplier_name": supplier.name,
                        "supplier_id": supplier.id,
                        "products": [],
                    }

                healthy_threshold = wp.threshold * healthy_multiplier
                suggested_qty = max(int(healthy_threshold - wp.quantity), 1)

                result[supplier.id]["products"].append({
                    "warehouse_product_id": wp.id,
                    "product_name": wp.product.name,
                    "warehouse_name": wp.warehouse.name,
                    "current_quantity": wp.quantity,
                    "threshold": wp.threshold,
                    "gap": suggested_qty,
                })
            except WarehouseProduct.DoesNotExist:
                continue

        return JsonResponse({"success": True, "data": list(result.values())})
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)})


@csrf_exempt
@login_required
@require_POST
@transaction.atomic
def confirm_create_po(request):
    try:
        data = json.loads(request.body)
        orders = data.get("orders", {})

        created_po_ids = []
        for supplier_id, items in orders.items():
            supplier = Supplier.objects.get(pk=supplier_id)
            po = PurchaseOrder.objects.create(supplier=supplier, status='DRAFT')
            created_po_ids.append(po.id)

            for item in items:
                wp = WarehouseProduct.objects.get(id=item["warehouse_product_id"])
                quantity = int(item["quantity"])
                price = wp.product.price  # 你可以改为其他定价逻辑

                PurchaseOrderItem.objects.create(
                    purchase_order=po,
                    item=wp,
                    quantity=quantity,
                    price=price,
                )

        return JsonResponse({
            "success": True,
            "latest_po_id": created_po_ids[-1] if created_po_ids else None
        })
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


@require_POST
def po_update(request, pk):
    purchase_order = get_object_or_404(PurchaseOrder, pk=pk)
    selected_status = request.POST.get('selected_status')

    updated = False

    status_flow = ['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED', 'DELIVERED', 'CANCELLED']
    previous_status = purchase_order.status

    # 更新 ETA 和 Status
    eta = request.POST.get('eta')
    if eta:
        purchase_order.eta = eta
        updated = True

    if selected_status and selected_status != previous_status:
        purchase_order.status = selected_status
        purchase_order.set_status_date(selected_status)

        prev_index = status_flow.index(previous_status)
        new_index = status_flow.index(selected_status)

        if new_index > prev_index:
            for skipped_status in status_flow[prev_index + 1:new_index]:
                date_field = f"{skipped_status.lower()}_date"
                if not getattr(purchase_order, date_field, None):
                    setattr(purchase_order, date_field, timezone.now())

        updated = True

    if updated:
        purchase_order.save()  # ✅ 正常 save()，不要用 update_fields，触发 auto_now字段！

    # 返回整个 purchase_orders_partial.html （重新渲染所有PO卡片）
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        purchase_orders = PurchaseOrder.objects.all().select_related('supplier').order_by('-last_updated_date')
        suppliers = Supplier.objects.all()

        status_choices = PurchaseOrder.STATUS_CHOICES
        next_statuses = {po.id: get_next_status(po.status) for po in purchase_orders}
        status_dates = {
            po.id: {code: getattr(po, f"{code.lower()}_date", None) for code, _ in status_choices}
            for po in purchase_orders
        }
        return render(request, 'warehouse/purchase_orders_partial.html', {
            'purchase_orders': purchase_orders,
            'suppliers': suppliers,
            'status_choices': status_choices,
            'next_statuses': next_statuses,
            'status_dates': status_dates,
        })

    return HttpResponse(status=204)


@require_POST
def po_edit_items(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)

    if not request.user.is_superuser:
        return HttpResponseForbidden()

    total_rows = int(request.POST.get("total_rows", 0))
    # 映射旧项目：使用 item.id（PurchaseOrderItem 的 id）
    existing_items = {str(item.id): item for item in po.items.all()}
    submitted_ids = []

    for i in range(total_rows + 10):  # 多扫几行，覆盖新增行情况
        item_id = request.POST.get(f"item_id_{i}")  # PurchaseOrderItem 的 id
        wp_id = request.POST.get(f"product_{i}")     # WarehouseProduct 的 id
        quantity = request.POST.get(f"quantity_{i}")
        price = request.POST.get(f"price_{i}")

        if not (wp_id and quantity and price):
            continue  # 如果缺字段，跳过该行

        if item_id and item_id in existing_items:
            # ✅ 更新旧的
            item = existing_items[item_id]
            item.item_id = wp_id  # WarehouseProduct 外键
            item.quantity = int(quantity)
            item.price = float(price)
            item.save()
            submitted_ids.append(item_id)
        elif not item_id:
            # ✅ 创建新的项
            new_item = PurchaseOrderItem.objects.create(
                purchase_order=po,
                item_id=wp_id,
                quantity=int(quantity),
                price=float(price),
            )
            submitted_ids.append(str(new_item.id))  # 把新建的 id 加入以防误删

    # ❗ 删除未出现在提交表单的旧项
    for old_item in po.items.all():
        if str(old_item.id) not in submitted_ids:
            old_item.delete()

    # 🔁 返回 PO 卡片部分 HTML
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        purchase_orders = PurchaseOrder.objects.all()
        suppliers = Supplier.objects.all()
        context = {
            "purchase_orders": purchase_orders,
            "suppliers": suppliers,
            "selected_supplier": request.GET.get("supplier", ""),
            "selected_status": request.GET.get("status", ""),
        }
        html = render_to_string("warehouse/purchase_orders_partial.html", context, request=request)
        return HttpResponse(html)

    return redirect('warehouse:warehouse_management')


@require_POST
def po_delete(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)

    if not request.user.is_superuser:
        return HttpResponseForbidden("You are not authorized to delete this PO.")

    po.delete()

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        # 🔁 获取当前筛选条件（从 query 参数中来）
        supplier_id = request.GET.get("supplier", "")
        status = request.GET.get("status", "")
        date_from = request.GET.get("date_from", "")
        date_to = request.GET.get("date_to", "")

        purchase_orders = PurchaseOrder.objects.all()
        if supplier_id:
            purchase_orders = purchase_orders.filter(supplier_id=supplier_id)
        if status:
            purchase_orders = purchase_orders.filter(status=status)
        if date_from:
            purchase_orders = purchase_orders.filter(order_date__gte=date_from)
        if date_to:
            purchase_orders = purchase_orders.filter(order_date__lte=date_to)

        context = {
            "purchase_orders": purchase_orders,
            "suppliers": Supplier.objects.all(),
            "selected_supplier": supplier_id,
            "selected_status": status,
            "date_from": date_from,
            "date_to": date_to,
        }

        html = render_to_string("warehouse/purchase_orders_partial.html", context, request=request)
        return HttpResponse(html)

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
