from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated

from django.shortcuts import render, get_object_or_404, redirect

from django.db import transaction
from django.db.models import Q

from inventory import serializers

from .models import (
    Supplier,
    Product,
    StockTransaction,
)

# ---------- DRF: API ViewSet ----------
class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = serializers.SupplierSerializer

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = serializers.ProductSerializer
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # 只显示当前登录用户的仓库库存
        user = self.request.user
        if user.warehouse:
            return Product.objects.filter(warehouse=user.warehouse)
        return Product.objects.none()

class StockTransactionViewSet(viewsets.ModelViewSet):
    queryset = StockTransaction.objects.all()
    serializer_class = serializers.StockTransactionSerializer


# ---------- UI : Inventory Management ----------
def inventory_management(request):
    # 根据查询参数选择哪个 tab，默认为 "products"
    tab = request.GET.get("tab", "product")
    # 根据 tab 选择不同的数据和模板
    if tab == "suppliers":
        suppliers = Supplier.objects.all()
        context = {"suppliers": suppliers, "active_tab": "suppliers"}
        partial_template = "inventory/supplier_list_partial.html"
    else:
        products = Product.objects.all().order_by('name')
        context = {"products": products, "active_tab": "products"}
        partial_template = "inventory/inventory_list_partial.html"

            # 如果是 Ajax 请求，只返回局部模板
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render(request, partial_template, context)

    # 非 Ajax 请求返回完整页面模板
    return render(request, "inventory/inventory_management.html", context)

def supplier_list(request):
    suppliers = Supplier.objects.all()
    return render(request, 'inventory/supplier_list.html', {'suppliers': suppliers})

def search(request):
    query = request.GET.get('q', '').strip()
    print("DEBUG: query =", query)  # 或使用 logging.info()

    results = Product.objects.select_related('supplier').filter(
            Q(sku__icontains=query) |
            Q(name__icontains=query) |
            Q(supplier__name__icontains=query)
            ).distinct().order_by('name')

    context = {
        'items': results,
        'query': query,
    }

    return render(request, "inventory/search_results.html", context)
