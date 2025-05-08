from django.contrib import admin
from .models import Order, Parcels

class ParcelsInline(admin.TabularInline):
    model = Parcels
    extra = 0
    fields = ('product', 'quantity', 'tracking_number')
    readonly_fields = ('tracking_number',)
    show_change_link = True

@admin.register(Order)
class Order(admin.ModelAdmin):
    list_display = ('id', 'customer', 'status', 'order_date', 'inventory_updated', 'tracking_info')
    list_filter = ('status', 'order_date')
    search_fields = ('id', 'customer')
    ordering = ('-order_date',)
    inlines = [ParcelsInline]

    def tracking_info(self, obj):
        if obj.tracking:
            return f"{obj.tracking.courier_name} - {obj.tracking.tracking_code}"
        return "No Tracking"
    tracking_info.short_description = "Tracking"

@admin.register(Parcels)
class ParcelsAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'product', 'quantity', 'tracking_number')
    search_fields = ('tracking_number', 'product__name', 'order__customer')
    list_filter = ('order__status',)
