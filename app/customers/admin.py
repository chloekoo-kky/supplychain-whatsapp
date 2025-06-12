# app/customers/admin.py

from django.contrib import admin
from .models import Customer

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('customer_id', 'customer_name', 'company_name', 'phone_number', 'city', 'country', 'updated_at')
    search_fields = ('customer_id', 'customer_name', 'company_name', 'phone_number', 'email', 'vat_number')
    list_filter = ('country', 'updated_at')
    readonly_fields = ('customer_id', 'created_at', 'updated_at')
    fieldsets = (
        ('Primary Information', {
            'fields': ('customer_id', 'customer_name', 'company_name')
        }),
        ('Contact Details', {
            'fields': ('phone_number', 'email', 'vat_number')
        }),
        ('Shipping Address', {
            'fields': ('address_line1', 'city', 'state', 'zip_code', 'country')
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
