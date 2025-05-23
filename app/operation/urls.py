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
]
