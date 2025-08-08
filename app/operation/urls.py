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
    path('create-orders-from-import/', views.create_orders_from_import, name='create_orders_from_import'),

    # The main list view now handles tabs internally
    path('list/', views.order_list, name='order_list'),
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
    path('parcel/<int:parcel_pk>/remove/', views.remove_parcel, name='remove_parcel'),

    # New URL for managing customs declarations
    path('parcel/<int:parcel_pk>/get-declarations/', views.get_declarations_for_courier, name='get_declarations_for_courier'),
    path('customs-declarations/', views.manage_customs_declarations, name='manage_customs_declarations'),
    path('customs-declarations/<int:pk>/edit/', views.edit_customs_declaration, name='edit_customs_declaration'),
    path('customs-declarations/<int:pk>/delete/', views.delete_customs_declaration, name='delete_customs_declaration'),
    # New URL for packaging management
    path('packaging/', views.packaging_management, name='packaging_management'),
    path('packaging/load-edit-form/<int:pk>/', views.load_edit_packaging_type_form, name='load_edit_packaging_type_form'),
    path('packaging/edit/<int:pk>/', views.edit_packaging_type, name='edit_packaging_type'),
    path('packaging/dashboard/<int:material_pk>/', views.get_packaging_stock_dashboard, name='get_packaging_stock_dashboard'),
    path('packaging/receipt-log/<int:material_pk>/', views.get_packaging_receipt_log, name='get_packaging_receipt_log'),

    path('customer/<int:customer_pk>/get-shipment-history/', views.get_customer_shipment_history, name='get_customer_shipment_history'),

    path('parcel/<int:parcel_pk>/get-airway-bill-details/', views.get_airway_bill_details, name='get_airway_bill_details'),
    path('parcel/<int:parcel_pk>/save-airway-bill/', views.save_airway_bill, name='save_airway_bill'),
    path('parcel/<int:parcel_pk>/get-tracking-history/', views.get_parcel_tracking_history, name='get_parcel_tracking_history'),

    path('parcels/trace-selected/', views.trace_selected_parcels, name='trace_selected_parcels'),
    path('parcels/print-selected/', views.print_selected_parcels, name='print_selected_parcels'),

    path('invoices/', views.courier_invoice_list, name='courier_invoice_list'),
    path('invoices/<int:pk>/edit/', views.edit_courier_invoice, name='edit_courier_invoice'),
    path('billing/cost-report/', views.cost_comparison_report, name='cost_comparison_report'),
    path('billing/parcels/', views.billed_parcels_list, name='billed_parcels_list'),
    path('billing/generate-client-invoice/<int:parcel_id>/', views.generate_client_invoice, name='generate_client_invoice'),

    path('reports/invoice-items/', views.invoice_item_report, name='invoice_item_report'),
    path('reports/invoice-items/load-more/', views.load_more_invoice_items, name='load_more_invoice_items'),

    path('invoice-item/<int:item_id>/view-parcel/', views.view_parcel_items, name='view_parcel_items'),
    path('invoice-item/<int:item_id>/dispute/', views.get_dispute_details, name='get_dispute_details'),
    path('invoice-item/<int:item_id>/dispute/save/', views.save_dispute_details, name='save_dispute_details'),
    path('invoice-item/<int:item_id>/cancel-dispute/', views.cancel_dispute, name='cancel_dispute'),

    path('generate-report/', views.generate_report, name='generate_report'),


]
