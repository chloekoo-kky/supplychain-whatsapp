# inventory/urls.py
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
    path('batches/export-excel/', views.export_inventory_batch_to_excel, name='export_inventory_batch_excel'),

    path('batch/add/', views.add_inventory_batch, name='add_inventory_batch'), # New
    path('batch/edit/<int:batch_pk>/', views.edit_inventory_batch, name='edit_inventory_batch'), # New
    path('batch/set-default-pick/<int:batch_pk>/', views.set_default_pick_view, name='set_default_pick'), # New URL
    path('stocktake/', views.stock_take_operator_view, name='stock_take_operator'),
    path('stocktake/search-warehouse-products/', views.search_warehouse_products_for_stocktake_json, name='stocktake_search_wp_json'), # New AJAX search URL
    # --- New Superuser Stock Take URLs ---
    path('stocktake/sessions/', views.stock_take_session_list_view, name='stock_take_session_list'),
    path('stock-take/upload/', views.upload_stock_take_csv, name='upload_stock_take_csv'),

    path('stocktake/session/<int:session_pk>/', views.stock_take_session_detail_view, name='stock_take_session_detail'),
    path('stocktake/session/<int:session_pk>/download/', views.download_stock_take_session_csv, name='download_stock_take_session_csv'),
    path('stocktake/session/<int:session_pk>/evaluate/', views.evaluate_stock_take_session_view, name='evaluate_stock_take_session'), # For later
    path('stocktake/session/<int:session_pk>/download-evaluation-excel/', views.download_stock_take_evaluation_excel, name='download_stock_take_evaluation_excel'),
    # --- ERP Stock Check URLs ---
    path('erp-check/upload/', views.upload_erp_stock_check_view, name='upload_erp_stock_check'),
    path('erp-check/sessions/', views.erp_stock_check_list_view, name='erp_stock_check_list'),
    path('erp-check/session/<int:session_pk>/', views.erp_stock_check_detail_view, name='erp_stock_check_detail'),
    path('erp-check/session/<int:session_pk>/evaluate/', views.evaluate_erp_stock_check_view, name='evaluate_erp_stock_check'),
    path('erp-check/session/<int:session_pk>/download-evaluation/', views.download_erp_evaluation_excel, name='download_erp_evaluation_excel'),
    # URLs for Default Pick Management
    path('default-picks/get/', views.get_default_pick_items, name='get_default_pick_items'),
    path('default-picks/update/', views.update_default_pick_items_view, name='update_default_pick_items'),
    path('batch/search-by-location/', views.search_batch_by_location_json_view, name='search_batch_by_location'),
    # URLs for Secondary Pick Management

]
