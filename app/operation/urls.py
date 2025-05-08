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
    path('management/', views.operation_management, name='operation_management'),
]
