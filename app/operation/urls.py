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
    path('list/', views.order_list_view, name='order_list'),
]
