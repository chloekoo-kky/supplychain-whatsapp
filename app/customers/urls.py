# app/customers/urls.py

from django.urls import path
from . import views

app_name = 'customers'

urlpatterns = [
    path('', views.customer_list_view, name='customer_list'),
]
