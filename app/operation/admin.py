from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import Order, OrderItem, Parcel, ParcelItem

# Inlines allow editing related models on the same page

class OrderItemInline(admin.TabularInline):
    """
    Inline for OrderItem model to be displayed within OrderAdmin.
    Allows adding/editing order items directly when viewing an order.
    """
    model = OrderItem
    fields = (
        'product',
        'warehouse_product',
        'erp_product_name',
        'quantity_ordered',
        'quantity_allocated',
        'quantity_shipped',
        'status',
        'is_cold_item',
        'suggested_batch_item',
        'notes'
    )
    readonly_fields = ('quantity_allocated', 'quantity_shipped') # Typically system-updated
    autocomplete_fields = ['product', 'warehouse_product', 'suggested_batch_item']
    extra = 1  # Number of empty forms to display
    # classes = ['collapse'] # Optional: to make inlines collapsible

class ParcelItemInline(admin.TabularInline):
    """
    Inline for ParcelItem model to be displayed within ParcelAdmin.
    Allows managing items within a specific parcel.
    """
    model = ParcelItem
    fields = ('order_item', 'shipped_from_batch', 'quantity_shipped_in_this_parcel')
    autocomplete_fields = ['order_item', 'shipped_from_batch']
    extra = 1
    # classes = ['collapse']

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Admin interface configuration for the Order model.
    """
    list_display = (
        'erp_order_id',
        'parcel_code',
        'customer_name_display',
        'warehouse_name_display',
        'order_date',
        'status',
        'is_cold_chain',
        'imported_at_formatted',
        'item_count'
    )
    list_filter = ('status', 'warehouse', 'is_cold_chain', 'order_date', 'imported_at')
    search_fields = (
        'erp_order_id',
        'parcel_code',
        'customer_name',
        'company_name',
        'warehouse__name',
        'items__product__sku', # Search by SKU of items in the order
        'items__product__name' # Search by name of items in the order
    )
    readonly_fields = (
        'parcel_code', # Auto-generated
        'imported_at',
        'last_updated_at',
        'imported_by',
        'processing_log_display' # Display for potentially long text
    )
    autocomplete_fields = ['warehouse', 'imported_by']
    inlines = [OrderItemInline]
    date_hierarchy = 'order_date' # Adds quick date navigation

    fieldsets = (
        (None, {
            'fields': ('erp_order_id', 'parcel_code', 'status', 'warehouse')
        }),
        ('Customer & Recipient Details', {
            'classes': ('collapse',), # Collapsible section
            'fields': (
                'customer_name', 'company_name',
                'recipient_address_line1', 'recipient_address_city',
                'recipient_address_state', 'recipient_address_zip', 'recipient_address_country',
                'recipient_phone', 'vat_number'
            )
        }),
        ('Order Dates & Import Info', {
            'classes': ('collapse',),
            'fields': ('order_date', 'imported_at', 'imported_by', 'last_updated_at')
        }),
        ('Shipping & Handling', {
            'classes': ('collapse',),
            'fields': ('is_cold_chain', 'courier_name', 'tracking_number', 'title_notes', 'shipping_notes')
        }),
        ('Processing Log', {
            'classes': ('collapse',),
            'fields': ('processing_log_display',)
        }),
    )

    def customer_name_display(self, obj):
        if obj.company_name:
            return f"{obj.customer_name} ({obj.company_name})"
        return obj.customer_name
    customer_name_display.short_description = 'Customer'
    customer_name_display.admin_order_field = 'customer_name'

    def warehouse_name_display(self, obj):
        return obj.warehouse.name if obj.warehouse else "-"
    warehouse_name_display.short_description = 'Warehouse'
    warehouse_name_display.admin_order_field = 'warehouse__name'

    def imported_at_formatted(self, obj):
        return obj.imported_at.strftime("%Y-%m-%d %H:%M") if obj.imported_at else "-"
    imported_at_formatted.short_description = 'Imported At'
    imported_at_formatted.admin_order_field = 'imported_at'

    def item_count(self, obj):
        return obj.items.count()
    item_count.short_description = 'Items'

    def processing_log_display(self, obj):
        # To prevent overly long text from breaking the admin page layout
        if obj.processing_log:
            return format_html("<pre style='white-space: pre-wrap; word-break: break-all; max-height: 200px; overflow-y: auto;'>{}</pre>", obj.processing_log)
        return "-"
    processing_log_display.short_description = 'Processing Log'


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    """
    Admin interface configuration for the OrderItem model.
    """
    list_display = (
        'order_link',
        'product_display',
        'warehouse_product_display',
        'quantity_ordered',
        'quantity_allocated',
        'quantity_shipped',
        'status',
        'is_cold_item'
    )
    list_filter = ('status', 'is_cold_item', 'order__warehouse', 'product__name')
    search_fields = (
        'order__erp_order_id',
        'order__parcel_code',
        'product__sku',
        'product__name',
        'erp_product_name',
        'warehouse_product__code'
    )
    readonly_fields = (
        'suggested_batch_number_display',
        'suggested_batch_expiry_date_display',
        # Quantities allocated/shipped are often system-managed
        'quantity_allocated',
        'quantity_shipped',
    )
    autocomplete_fields = ['order', 'product', 'warehouse_product', 'suggested_batch_item']
    list_select_related = ('order', 'product', 'warehouse_product', 'warehouse_product__warehouse', 'suggested_batch_item')

    fieldsets = (
        (None, {
            'fields': ('order', 'product', 'warehouse_product', 'erp_product_name')
        }),
        ('Quantities', {
            'fields': ('quantity_ordered', 'quantity_allocated', 'quantity_shipped')
        }),
        ('Batch & Handling', {
            'fields': ('suggested_batch_item', 'suggested_batch_number_display', 'suggested_batch_expiry_date_display', 'is_cold_item')
        }),
        ('Status & Notes', {
            'fields': ('status', 'notes')
        }),
    )

    def order_link(self, obj):
        if obj.order:
            link = reverse("admin:operation_order_change", args=[obj.order.id])
            return format_html('<a href="{}">{}</a>', link, obj.order.erp_order_id)
        return "-"
    order_link.short_description = 'Order (ERP ID)'
    order_link.admin_order_field = 'order__erp_order_id'

    def product_display(self, obj):
        return obj.product.name if obj.product else obj.erp_product_name
    product_display.short_description = 'Product'
    product_display.admin_order_field = 'product__name'

    def warehouse_product_display(self, obj):
        if obj.warehouse_product:
            return f"{obj.warehouse_product.product.sku} @ {obj.warehouse_product.warehouse.name}"
        return "-"
    warehouse_product_display.short_description = 'Stock Item (SKU @ WH)'
    warehouse_product_display.admin_order_field = 'warehouse_product__product__sku'


@admin.register(Parcel)
class ParcelAdmin(admin.ModelAdmin):
    """
    Admin interface configuration for the Parcel model.
    """
    list_display = (
        'order_link',
        'parcel_code_system_display',
        'courier_name',
        'tracking_number',
        'shipped_at_formatted',
        'created_at_formatted',
        'item_in_parcel_count'
    )
    list_filter = ('courier_name', 'shipped_at', 'order__warehouse', 'created_at')
    search_fields = (
        'order__erp_order_id',
        'order__parcel_code',
        'parcel_code_system',
        'tracking_number',
        'courier_name'
    )
    readonly_fields = ('created_at',)
    autocomplete_fields = ['order']
    inlines = [ParcelItemInline]
    date_hierarchy = 'created_at'

    fieldsets = (
        (None, {
            'fields': ('order', 'parcel_code_system')
        }),
        ('Shipment Details', {
            'fields': ('courier_name', 'tracking_number', 'shipped_at')
        }),
        ('Notes & Timestamps', {
            'fields': ('notes', 'created_at')
        }),
    )

    def order_link(self, obj):
        if obj.order:
            link = reverse("admin:operation_order_change", args=[obj.order.id])
            return format_html('<a href="{}">{} (Parcel: {})</a>', link, obj.order.erp_order_id, obj.order.parcel_code)
        return "-"
    order_link.short_description = 'Order (ERP ID)'
    order_link.admin_order_field = 'order__erp_order_id'

    def parcel_code_system_display(self, obj):
        return obj.parcel_code_system or "N/A"
    parcel_code_system_display.short_description = 'System Parcel Code'

    def shipped_at_formatted(self, obj):
        return obj.shipped_at.strftime("%Y-%m-%d %H:%M") if obj.shipped_at else "-"
    shipped_at_formatted.short_description = 'Shipped At'
    shipped_at_formatted.admin_order_field = 'shipped_at'

    def created_at_formatted(self, obj):
        return obj.created_at.strftime("%Y-%m-%d %H:%M") if obj.created_at else "-"
    created_at_formatted.short_description = 'Created At'
    created_at_formatted.admin_order_field = 'created_at'

    def item_in_parcel_count(self, obj):
        return obj.items_in_parcel.count()
    item_in_parcel_count.short_description = 'Items in Parcel'


@admin.register(ParcelItem)
class ParcelItemAdmin(admin.ModelAdmin):
    """
    Admin interface configuration for the ParcelItem model.
    """
    list_display = (
        'parcel_link',
        'order_item_display',
        'quantity_shipped_in_this_parcel',
        'shipped_from_batch_display'
    )
    list_filter = ('parcel__courier_name', 'order_item__product__name', 'parcel__order__warehouse')
    search_fields = (
        'parcel__tracking_number',
        'parcel__order__erp_order_id',
        'order_item__product__sku',
        'shipped_from_batch__batch_number'
    )
    autocomplete_fields = ['parcel', 'order_item', 'shipped_from_batch']
    list_select_related = (
        'parcel',
        'parcel__order',
        'order_item',
        'order_item__product',
        'shipped_from_batch',
        'shipped_from_batch__warehouse_product__product'
    )

    def parcel_link(self, obj):
        if obj.parcel:
            link = reverse("admin:operation_parcel_change", args=[obj.parcel.id])
            return format_html('<a href="{}">Parcel for Order {}</a>', link, obj.parcel.order.erp_order_id)
        return "-"
    parcel_link.short_description = 'Parcel'
    parcel_link.admin_order_field = 'parcel__order__erp_order_id'

    def order_item_display(self, obj):
        if obj.order_item and obj.order_item.product:
            return f"{obj.order_item.product.sku} (Order: {obj.order_item.order.erp_order_id})"
        elif obj.order_item:
            return f"Item for Order: {obj.order_item.order.erp_order_id}"
        return "-"
    order_item_display.short_description = 'Order Item'
    order_item_display.admin_order_field = 'order_item__product__sku' # or order_item__order__erp_order_id

    def shipped_from_batch_display(self, obj):
        if obj.shipped_from_batch:
            return f"Batch: {obj.shipped_from_batch.batch_number} (SKU: {obj.shipped_from_batch.warehouse_product.product.sku})"
        return "-"
    shipped_from_batch_display.short_description = 'Shipped from Batch'
    shipped_from_batch_display.admin_order_field = 'shipped_from_batch__batch_number'

