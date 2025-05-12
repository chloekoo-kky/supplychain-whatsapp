# ===== warehouse/urls.py =====
from django.urls import path
from warehouse import views

app_name = 'warehouse'

urlpatterns = [
    # Main management page
    path('management/', views.warehouse_management, name='warehouse_management'),    path('search/', views.search, name='search'),

    # AJAX endpoint for filtering POs (this one returns JSON)
    path('purchaseorders/filter-data/', views.get_filtered_po_data, name='get_filtered_po_data'),

    # AJAX endpoint for loading more POs (this one returns HTML partial)
    path('po-load-more/', views.load_more_pos, name='po_load_more'),

    # URLs for Purchase Order actions (ensure these match your form actions)
    path('po/<int:pk>/update/', views.po_update, name='po_update'),
    path('po/<int:pk>/edit-items/', views.po_edit_items, name='po_edit_items'),
    path('po/<int:pk>/delete/', views.po_delete, name='po_delete'),

    # URLs for PO Receipt Modals
    path('po/<int:po_id>/get-items-for-receiving/', views.get_po_items_for_receiving, name='get_po_items_for_receiving'),
    path('po/<int:po_id>/process-receipt/', views.process_po_receipt, name='process_po_receipt'),

    # Other URLs from your views.py that need to be accessible
    path('search/', views.search, name='search_warehouse_products'), # Example, adjust if needed
    path('warehouseproduct/<int:pk>/details/', views.warehouseproduct_details, name='warehouseproduct_details'),
    path('prepare-po/', views.prepare_po_from_selection, name='prepare_po_from_selection'),
    path('confirm-create-po/', views.confirm_create_po, name='confirm_create_po'),

    path('update-stock/', views.update_stock, name='update_stock'),

]
