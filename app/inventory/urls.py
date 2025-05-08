'''
URL mappings for the inventory app.
'''
from django.urls import (
    path,
    include,
)

from rest_framework.routers import DefaultRouter

from inventory import views


router = DefaultRouter()
router.register('inventorytransaction', views.StockTransactionViewSet)
router.register('suppliers', views.SupplierViewSet)

app_name = 'inventory'


urlpatterns = [
    path('', include(router.urls)),
    path('management/', views.inventory_management, name='inventory_management'),
    path('supplierlist/', views.supplier_list, name='supplier_list'),
    path('search/', views.search, name='search'),
]
