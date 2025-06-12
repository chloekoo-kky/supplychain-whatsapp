# app/operation/admin.py
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import Order, OrderItem, Parcel, ParcelItem, CustomsDeclaration, CourierCompany, PackagingType, PackagingTypeMaterialComponent
from inventory.models import PackagingMaterial
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
        'quantity_packed', # Added
        'quantity_shipped',
        'status',
        'is_cold_item',
        'suggested_batch_item',
        'notes'
    )
    readonly_fields = ('quantity_packed', 'quantity_shipped', 'status') # Status is often system-managed
    autocomplete_fields = ['product', 'warehouse_product', 'suggested_batch_item']
    extra = 1
    # classes = ['collapse']

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
    Admin interface configuration for the Order model, updated for the new
    Customer model relationship.
    """
    list_display = (
        'erp_order_id',
        'customer_display', # UPDATED
        'warehouse_name_display',
        'order_date',
        'status',
        'is_cold_chain',
        'imported_at_formatted',
        'item_count',
        'parcel_count_display'
    )
    list_filter = ('status', 'warehouse', 'is_cold_chain', 'order_date')
    search_fields = (
        'erp_order_id',
        # UPDATED: Search through the customer relationship
        'customer__customer_id',
        'customer__customer_name',
        'customer__company_name',
        'customer__phone_number',
        'customer__email',
        'items__product__sku',
        'parcels__parcel_code_system',
    )
    readonly_fields = (
        'erp_order_id', # Keep ERP ID read-only after creation
        'imported_at',
        'last_updated_at',
        'imported_by',
        'processing_log_display'
    )
    # UPDATED: Add 'customer' to enable search widget
    autocomplete_fields = ['warehouse', 'imported_by', 'customer']
    inlines = [OrderItemInline]
    date_hierarchy = 'order_date'

    fieldsets = (
        (None, {
            'fields': ('erp_order_id', 'status', 'warehouse')
        }),
        # UPDATED: Replaced multiple text fields with a single, searchable link
        ('Customer Information', {
            'fields': ('customer',)
        }),
        ('Order Dates & Import Info', {
            'classes': ('collapse',),
            'fields': ('order_date', 'imported_at', 'imported_by', 'last_updated_at')
        }),
        ('Shipping & Handling (Order Level)', {
            'classes': ('collapse',),
            'fields': ('is_cold_chain', 'title_notes', 'shipping_notes')
        }),
        ('Processing Log', {
            'classes': ('collapse',),
            'fields': ('processing_log_display',)
        }),
    )

    # UPDATED: This method now gets data from the linked customer object
    @admin.display(description='Customer', ordering='customer__customer_name')
    def customer_display(self, obj):
        if obj.customer:
            if obj.customer.company_name:
                return f"{obj.customer.customer_name} ({obj.customer.company_name})"
            return obj.customer.customer_name
        return "N/A"

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

    def parcel_count_display(self, obj):
        return obj.parcels.count()
    parcel_count_display.short_description = 'Parcels'

    def processing_log_display(self, obj):
        if obj.processing_log:
            return format_html("<pre style='white-space: pre-wrap; word-break: break-all; max-height: 200px; overflow-y: auto;'>{}</pre>", obj.processing_log)
        return "-"
    processing_log_display.short_description = 'Processing Log'


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        'order_link',
        'product_display',
        'warehouse_product_display',
        'quantity_ordered',
        'quantity_packed', # Added
        'quantity_shipped',
        'status',
        'is_cold_item'
    )
    list_filter = ('status', 'is_cold_item', 'order__warehouse', 'product__name')
    search_fields = (
        'order__erp_order_id',
        'order__order_display_code', # Changed
        'product__sku',
        'product__name',
        'erp_product_name',
        'warehouse_product__code'
    )
    readonly_fields = (
        'suggested_batch_number_display',
        'suggested_batch_expiry_date_display',
        'quantity_packed', # Added
        'quantity_shipped',
    )
    autocomplete_fields = ['order', 'product', 'warehouse_product', 'suggested_batch_item']
    list_select_related = ('order', 'product', 'warehouse_product', 'warehouse_product__warehouse', 'suggested_batch_item')

    fieldsets = (
        (None, {
            'fields': ('order', 'product', 'warehouse_product', 'erp_product_name')
        }),
        ('Quantities', {
            'fields': ('quantity_ordered', 'quantity_packed', 'quantity_shipped') # Added quantity_packed
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
    list_display = (
        'order_link',
        'parcel_code_system', # Changed display name consistency
        'courier_company',
        'tracking_number',
        'shipped_at_formatted',
        'created_at_formatted',
        'item_in_parcel_count',
        'created_by_display' # Added
    )
    list_filter = ('courier_company', 'shipped_at', 'order__warehouse', 'created_at', 'created_by')
    search_fields = (
        'order__erp_order_id',
        'order__order_display_code', # Changed
        'parcel_code_system',
        'tracking_number',
        'courier_company'
    )
    readonly_fields = ('created_at','parcel_code_system', 'created_by') # parcel_code_system is auto-gen
    autocomplete_fields = ['order'] # created_by is set automatically in view
    inlines = [ParcelItemInline]
    date_hierarchy = 'created_at'

    fieldsets = (
        (None, {
            'fields': ('order', 'parcel_code_system')
        }),
        ('Shipment Details', {
            'fields': ('courier_company', 'tracking_number', 'shipped_at')
        }),
        ('Notes & Timestamps', {
            'fields': ('notes', 'created_at', 'created_by') # Added created_by
        }),
    )

    def order_link(self, obj):
        if obj.order:
            link = reverse("admin:operation_order_change", args=[obj.order.id])
            return format_html('<a href="{}">{}</a>', link, obj.order.erp_order_id) # Display ERP ID in link
        return "-"
    order_link.short_description = 'Order (ERP ID)'
    order_link.admin_order_field = 'order__erp_order_id'

    # Removed parcel_code_system_display as parcel_code_system is fine by itself.

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

    def created_by_display(self, obj):
        return obj.created_by.name if obj.created_by and obj.created_by.name else (obj.created_by.email if obj.created_by else "N/A")
    created_by_display.short_description = "Created By"
    created_by_display.admin_order_field = 'created_by__name'


@admin.register(ParcelItem)
class ParcelItemAdmin(admin.ModelAdmin):
    list_display = (
        'parcel_link',
        'order_item_display',
        'quantity_shipped_in_this_parcel',
        'shipped_from_batch_display'
    )
    list_filter = ('parcel__courier_company', 'order_item__product__name', 'parcel__order__warehouse')
    search_fields = (
        'parcel__tracking_number',
        'parcel__parcel_code_system',
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
            return format_html('<a href="{}">Parcel {}</a>', link, obj.parcel.parcel_code_system) # Display parcel code
        return "-"
    parcel_link.short_description = 'Parcel'
    parcel_link.admin_order_field = 'parcel__parcel_code_system' # Order by parcel code

    def order_item_display(self, obj):
        if obj.order_item and obj.order_item.product:
            return f"{obj.order_item.product.sku} (Order: {obj.order_item.order.erp_order_id})"
        elif obj.order_item:
            return f"Item for Order: {obj.order_item.order.erp_order_id}"
        return "-"
    order_item_display.short_description = 'Order Item'
    order_item_display.admin_order_field = 'order_item__product__sku'

    def shipped_from_batch_display(self, obj):
        if obj.shipped_from_batch:
            return f"Batch: {obj.shipped_from_batch.batch_number} (SKU: {obj.shipped_from_batch.warehouse_product.product.sku})"
        return "-"
    shipped_from_batch_display.short_description = 'Shipped from Batch'
    shipped_from_batch_display.admin_order_field = 'shipped_from_batch__batch_number'

@admin.register(CourierCompany)
class CourierCompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'updated_at')
    search_fields = ('name', 'code')

@admin.register(CustomsDeclaration)
class CustomsDeclarationAdmin(admin.ModelAdmin):
    list_display = ('description', 'hs_code', 'get_courier_companies_display', 'get_shipment_types_display', 'updated_at')
    search_fields = ('description', 'hs_code', 'courier_companies__name', 'courier_companies__code')
    list_filter = ('courier_companies', 'applies_to_ambient', 'applies_to_cold_chain', 'applies_to_mix', 'updated_at')
    filter_horizontal = ('courier_companies',) # Better UI for ManyToManyField
    fieldsets = (
        (None, {
            'fields': ('description', 'hs_code', 'notes')
        }),
        ('Applicability', {
            'fields': ('courier_companies', ('applies_to_ambient', 'applies_to_cold_chain', 'applies_to_mix'))
        }),
    )

    def get_courier_companies_display(self, obj):
        return ", ".join([c.name for c in obj.courier_companies.all()])
    get_courier_companies_display.short_description = 'Courier Companies'

    def get_shipment_types_display(self, obj):
        return obj.get_shipment_types_display()
    get_shipment_types_display.short_description = 'Shipment Types'

class PackagingTypeMaterialComponentInline(admin.TabularInline):
    """
    Inline admin for managing the materials and quantities that make up a PackagingType.
    """
    model = PackagingTypeMaterialComponent
    fields = ('packaging_material', 'quantity')
    autocomplete_fields = ['packaging_material']
    extra = 1
    verbose_name = "Material Component"
    verbose_name_plural = "Material Components"

@admin.register(PackagingType)
class PackagingTypeAdmin(admin.ModelAdmin):
    """
    Admin interface configuration for the PackagingType model.
    """
    list_display = (
        'name',
        'type_code',
        'environment_type',
        'default_length_cm',
        'default_width_cm',
        'default_height_cm',
        'is_active'
    )
    list_filter = ('environment_type', 'is_active')
    # Corrected: Removed 'legacy_code' from search_fields as it does not exist on the model
    search_fields = ('name', 'type_code')

    fieldsets = (
        (None, {
            'fields': ('name', 'type_code', 'is_active')
        }),
        ('Type & Dimensions', {
            'fields': ('environment_type', ('default_length_cm', 'default_width_cm', 'default_height_cm'))
        }),
        ('Description', {
            'classes': ('collapse',),
            'fields': ('description',)
        }),
    )
    inlines = [PackagingTypeMaterialComponentInline]



