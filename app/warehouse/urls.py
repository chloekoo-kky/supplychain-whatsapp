# ===== warehouse/urls.py =====
from django.urls import path
from warehouse import views

app_name = 'warehouse'

urlpatterns = [
    path('management/', views.warehouse_management, name='warehouse_management'),
    path('search/', views.search, name='search'),
    path('warehouseproduct/<int:pk>/details/', views.warehouseproduct_details, name='warehouseproduct_details'),
    path('prepare-po/', views.prepare_po_from_selection, name='prepare_po_from_selection'),  # ✅预览modal用
    path('confirm-create-po/', views.confirm_create_po, name='confirm_create_po'),  # ✅确认提交用
    path('purchaseorders/filter/', views.purchase_order_list_partial, name='po_filter_list_partial'),
    path('po-update/<int:pk>/', views.po_update, name='po_update'),
    path('po/<int:pk>/edit-items/', views.po_edit_items, name='po_edit_items'),
    path('po/<int:pk>/delete/', views.po_delete, name='po_delete'),
    path('update-stock/', views.update_stock, name='update_stock'),
]
