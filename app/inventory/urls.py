'''
URL mappings for the inventory app.
'''
from django.urls import (
    path,
    include,
)

from inventory import views


app_name = 'inventory'


urlpatterns = [
    path('batchlist/', views.inventory_batch_list_view, name='inventory_batch_list_view'),
    path('batch/add/', views.add_inventory_batch_view, name='add_inventory_batch'), # New
    path('batch/edit/<int:batch_pk>/', views.edit_inventory_batch_view, name='edit_inventory_batch'), # New
    path('supplierlist/', views.supplier_list, name='supplier_list'),
    path('stocktake/', views.stock_take_operator_view, name='stock_take_operator'),
    path('stocktake/search-warehouse-products/', views.search_warehouse_products_for_stocktake_json, name='stocktake_search_wp_json'), # New AJAX search URL
    # --- New Superuser Stock Take URLs ---
    path('stocktake/sessions/', views.stock_take_session_list_view, name='stock_take_session_list'),
    path('stocktake/session/<int:session_pk>/', views.stock_take_session_detail_view, name='stock_take_session_detail'),
    path('stocktake/session/<int:session_pk>/download/', views.download_stock_take_session_csv, name='download_stock_take_session_csv'),
    path('stocktake/session/<int:session_pk>/evaluate/', views.evaluate_stock_take_session_view, name='evaluate_stock_take_session'), # For later
    path('stocktake/session/<int:session_pk>/download-evaluation-excel/', views.download_stock_take_evaluation_excel, name='download_stock_take_evaluation_excel'),

]
