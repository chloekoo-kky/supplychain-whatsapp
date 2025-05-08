from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated

from django.shortcuts import render, get_object_or_404, redirect

from django.views.decorators.http import require_POST
from django.contrib import messages
from django.db import transaction
from django.db.models import Q

from operation import serializers

from .models import Order, Parcels

from inventory import models as inventory_models

# ---------- DRF: API ViewSet ----------
class Order(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = serializers.OrderSerializer


# ---------- UI : Operation Management ----------
def operation_management(request):
    # 根据查询参数选择哪个 tab，默认为 "products"
    tab = request.GET.get("tab", "orders")
    # 根据 tab 选择不同的数据和模板
    if tab == "parcels":
        parcels = Parcels.objects.all()
        context = {"parcels": parcels, "active_tab": "parcels"}
        partial_template = "operation/parcels_list_partial.html"
    elif tab == "tracking":
        tracking = Tracking.objects.all()
        context = {"tracking": tracking, "active_tab": "tracking"}
        partial_template = "operation/tracking_list_partial.html"
    else:
        orders = Order.objects.all()
        context = {"orders": orders, "active_tab": "orders"}
        partial_template = "operation/order_list_partial.html"

            # 如果是 Ajax 请求，只返回局部模板
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render(request, partial_template, context)

    # 非 Ajax 请求返回完整页面模板
    return render(request, "operation/operation_management.html", context)


def order_list(request):
    orders = Order.objects.prefetch_related('parcels', 'items').all()

    for order in orders:
        # 计算每个产品已经发了多少
        shipped = {}
        for parcel in order.parcels.all():
            product_id = parcel.product.id
            shipped[product_id] = shipped.get(product_id, 0) + parcel.quantity
        order.shipped_quantities = shipped  # 把发货数量字典绑在每个order上

    return render(request, 'operation/order_list.html', {'orders': orders})


def parcels_list(request):
    supplier_id = request.GET.get('supplier')
    status = request.GET.get('status')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    purchase_orders = inventory_models.PurchaseOrder.objects.all()

    if supplier_id:
        purchase_orders = purchase_orders.filter(supplier_id=supplier_id)
    if status:
        purchase_orders = purchase_orders.filter(status=status)
    if date_from:
        purchase_orders = purchase_orders.filter(order_date__gte=date_from)
    if date_to:
        purchase_orders = purchase_orders.filter(order_date__lte=date_to)

    suppliers = inventory_models.Supplier.objects.all()

    return render(request, 'operation/parcels_list_partial.html', {
        'purchase_orders': purchase_orders,
        'suppliers': suppliers,
        'selected_supplier': supplier_id,
        'selected_status': status,
        'date_from': date_from,
        'date_to': date_to,
    })

def parcels_list(request):
    supplier_id = request.GET.get('supplier')
    status = request.GET.get('status')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    purchase_orders = inventory_models.PurchaseOrder.objects.all()

    if supplier_id:
        purchase_orders = purchase_orders.filter(supplier_id=supplier_id)
    if status:
        purchase_orders = purchase_orders.filter(status=status)
    if date_from:
        purchase_orders = purchase_orders.filter(order_date__gte=date_from)
    if date_to:
        purchase_orders = purchase_orders.filter(order_date__lte=date_to)

    suppliers = inventory_models.Supplier.objects.all()

    return render(request, 'operation/tracking_list_partial.html', {
        'purchase_orders': purchase_orders,
        'suppliers': suppliers,
        'selected_supplier': supplier_id,
        'selected_status': status,
        'date_from': date_from,
        'date_to': date_to,
    })
