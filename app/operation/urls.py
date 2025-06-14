'''
URL mappings for the operation app.
'''
from django.urls import (
    path,
    include,
)

from operation import views

app_name = 'operation'

urlpatterns = [
    path('import-excel/', views.import_orders_from_excel, name='import_orders_from_excel'),
    # The main list view now handles tabs internally
    path('list/', views.order_list_view, name='order_list'),
    # AJAX endpoint to get items for packing modal
    path('order/<int:order_pk>/get-packing-items/', views.get_order_items_for_packing, name='get_order_items_for_packing'),
    # Endpoint to process the packing form submission
    path('order/<int:order_pk>/process-packing/', views.process_packing_for_order, name='process_packing_for_order'),
    # AJAX endpoint to get available batches for a specific order item
    path('order-item/<int:order_item_pk>/get-available-batches/', views.get_available_batches_for_order_item, name='get_available_batches_for_order_item'),
    path('order/<int:order_pk>/get-items-for-editing/', views.get_order_items_for_editing, name='get_order_items_for_editing'),
    path('order/<int:order_pk>/process-item-removal/', views.process_order_item_removal, name='process_order_item_removal'),
    # New URL for loading more customer orders
    path('customer-orders/load-more/', views.load_more_customer_orders, name='load_more_customer_orders'),
    # New URLs for Parcel View/Edit Modal
    path('parcel/<int:parcel_pk>/get-details-for-editing/', views.get_parcel_details_for_editing, name='get_parcel_details_for_editing'),
    path('parcel/<int:parcel_pk>/update-customs-details/', views.update_parcel_customs_details, name='update_parcel_customs_details'),
    # New URL for managing customs declarations
    path('customs-declarations/', views.manage_customs_declarations, name='manage_customs_declarations'),
    path('customs-declarations/<int:pk>/edit/', views.edit_customs_declaration, name='edit_customs_declaration'),
    path('customs-declarations/<int:pk>/delete/', views.delete_customs_declaration, name='delete_customs_declaration'),
    # New URL for packaging management
    path('packaging/', views.packaging_management, name='packaging_management'),
    path('packaging/load-edit-form/<int:pk>/', views.load_edit_packaging_type_form, name='load_edit_packaging_type_form'),
    path('packaging/edit/<int:pk>/', views.edit_packaging_type, name='edit_packaging_type'),

    path('customer/<int:customer_pk>/get-shipment-history/', views.get_customer_shipment_history, name='get_customer_shipment_history'),
]
