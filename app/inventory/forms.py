# inventory/forms.py
from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet, formset_factory, BaseFormSet
from .models import InventoryBatchItem, StockTakeSession, StockTakeItem
from warehouse.models import Warehouse, WarehouseProduct # Assuming WarehouseProduct is in warehouse.models

class InventoryBatchItemForm(forms.ModelForm):
    class Meta:
        model = InventoryBatchItem
        fields = [
            'warehouse_product',
            'batch_number',
            'location_label',
            'expiry_date',
            'quantity',
            'cost_price',
            'date_received',
        ]
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date', 'class': 'input input-bordered w-full'}),
            'date_received': forms.DateInput(attrs={'type': 'date', 'class': 'input input-bordered w-full'}),
            'warehouse_product': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'batch_number': forms.TextInput(attrs={'placeholder': 'eg BATCH001 (Required)', 'class': 'input input-bordered w-full'}),
            'location_label': forms.TextInput(attrs={'placeholder': 'eg A01-01', 'class': 'input input-bordered w-full'}),
            'quantity': forms.NumberInput(attrs={'min': '0', 'class': 'input input-bordered w-full'}),
            'cost_price': forms.NumberInput(attrs={'step': '0.01', 'placeholder': 'eg 10.50 (Optional)', 'class': 'input input-bordered w-full'}),

        }

    def __init__(self, *args, **kwargs):
        # Pop 'request' if passed from the view, to access request.user
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        user = self.request.user if self.request else None


        # Filter warehouse_product queryset based on user
        if user and not user.is_superuser and user.warehouse:
            self.fields['warehouse_product'].queryset = WarehouseProduct.objects.filter(
                warehouse=user.warehouse
            ).select_related('product', 'warehouse').order_by('product__name', 'warehouse__name')
        else: # Superuser or user with no warehouse (though latter should ideally not reach this form with ability to select)
             # Or if request is not available (e.g. when form is instantiated outside a view context)
            self.fields['warehouse_product'].queryset = WarehouseProduct.objects.all(
            ).select_related('product', 'warehouse').order_by('product__name', 'warehouse__name')

        # Set a user-friendly label format for the warehouse_product dropdown
        self.fields['warehouse_product'].label_from_instance = lambda obj: f"{obj.product.name} @ {obj.warehouse.name} (SKU: {obj.product.sku})"

        # Set fields as not required if their model field has blank=True
        if self.fields.get('expiry_date') and self.Meta.model._meta.get_field('expiry_date').blank:
            self.fields['expiry_date'].required = True

        if self.fields.get('location_label') and self.Meta.model._meta.get_field('location_label').blank:
            self.fields['location_label'].required = False

        if self.fields.get('cost_price') and self.Meta.model._meta.get_field('cost_price').blank:
            self.fields['cost_price'].required = False

        if self.fields.get('batch_number'): # Batch number required status based on model
             self.fields['batch_number'].required = not self.Meta.model._meta.get_field('batch_number').blank


    def clean_location_label(self):
        location_label = self.cleaned_data.get('location_label', '')
        return None if not location_label.strip() else location_label.strip()

    def clean(self):
        cleaned_data = super().clean()
        # warehouse_product = cleaned_data.get('warehouse_product')
        # batch_number = cleaned_data.get('batch_number')
        # location_label = cleaned_data.get('location_label')

        # if warehouse_product and batch_number: # location_label can be None
        #     filter_kwargs = {
        #         'warehouse_product': warehouse_product,
        #         'batch_number': batch_number,
        #         'location_label': location_label
        #     }

        #     queryset = InventoryBatchItem.objects.filter(**filter_kwargs)

        #     if self.instance and self.instance.pk:
        #         queryset = queryset.exclude(pk=self.instance.pk)

        #     if queryset.exists():
        #         loc_display = f"at location '{location_label}'" if location_label else "with no specific location label"
        #         # Check if the existing item is the one being edited, if so, it's not a conflict for itself.
        #         # This is already handled by `queryset = queryset.exclude(pk=self.instance.pk)` if self.instance.pk exists.
        #         # So, if queryset still exists, it's a conflict with another record.
        #         raise forms.ValidationError(
        #             f"An inventory batch item with batch number '{batch_number}' for this product/warehouse {loc_display} already exists."
        #         )
        return cleaned_data


class StockTakeItemForm(forms.ModelForm):
    product_code_input = forms.CharField(
        max_length=100,
        required=True,
        label="Product Code/SKU",
        widget=forms.TextInput(attrs={
            'class': 'input input-sm input-bordered w-full product-code-input',
            'placeholder': 'Enter Code or SKU'
        })
    )

    class Meta:
        model = StockTakeItem
        fields = [
            'warehouse_product',
            'product_code_input',
            'location_label_counted',
            'batch_number_counted',
            'expiry_date_counted',
            'counted_quantity',
            'notes'
        ]
        widgets = {
            'warehouse_product': forms.HiddenInput(attrs={'class': 'selected-warehouse-product-id'}),
            'location_label_counted': forms.TextInput(attrs={'class': 'input input-sm input-bordered w-full', 'placeholder': 'e.g., A01-01'}),
            'batch_number_counted': forms.TextInput(attrs={'class': 'input input-sm input-bordered w-full', 'placeholder': 'Batch No.'}),
            'expiry_date_counted': forms.DateInput(attrs={'type': 'date', 'class': 'input input-sm input-bordered w-full'}),
            'counted_quantity': forms.NumberInput(attrs={'class': 'input input-sm input-bordered w-full', 'min': '0', 'required': True}),
            'notes': forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': '1', 'placeholder': 'Notes'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.warehouse = kwargs.pop('warehouse', None)
        super().__init__(*args, **kwargs)

        if self.warehouse:
            self.fields['warehouse_product'].queryset = WarehouseProduct.objects.filter(warehouse=self.warehouse)
        elif self.user and self.user.is_superuser:
            self.fields['warehouse_product'].queryset = WarehouseProduct.objects.all()
        else:
            self.fields['warehouse_product'].queryset = WarehouseProduct.objects.none()

        for field_name in ['location_label_counted', 'batch_number_counted', 'expiry_date_counted', 'counted_quantity', 'notes']:
            if field_name in self.fields:
                model_field = self.Meta.model._meta.get_field(field_name)
                if hasattr(model_field, 'blank'):
                    self.fields[field_name].required = not model_field.blank

        self.fields['counted_quantity'].required = True

        if self.instance and self.instance.pk and self.instance.warehouse_product:
            wp = self.instance.warehouse_product
            self.initial['product_code_input'] = wp.code if wp.code else wp.product.sku

    def clean_product_code_input(self):
        code = self.cleaned_data.get('product_code_input')
        if not code:
            raise forms.ValidationError("Product Code/SKU is required.")
        return code

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.pk and not cleaned_data.get('warehouse_product'):
            self.add_error('product_code_input', "Please select a valid product using the code/SKU search.")
        return cleaned_data

class BaseStockTakeItemFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.warehouse = kwargs.pop('warehouse', None)
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.user = self.user
            form.warehouse = self.warehouse

StockTakeItemFormSet = inlineformset_factory(
    StockTakeSession,
    StockTakeItem,
    form=StockTakeItemForm,
    formset=BaseStockTakeItemFormSet,
    fields=[
        'warehouse_product',
        'location_label_counted',
        'batch_number_counted',
        'expiry_date_counted',
        'counted_quantity',
        'notes'
    ],
    extra=0,  # MODIFIED: Start with 3 empty forms
    can_delete=True,
    can_order=False
)

class StockTakeSessionSelectionForm(forms.Form):
    active_session = forms.ModelChoiceField(
        queryset=StockTakeSession.objects.none(),
        required=False,
        label="Select an ongoing Stock Take Session",
        empty_label="-- Start a New Session --",
        widget=forms.Select(attrs={'class': 'select select-bordered w-full mb-4', 'id': 'id_active_session_select'})
    )
    session_name = forms.CharField(
        max_length=255,
        required=False,
        label="New Session Name (if starting new)",
        help_text="DATE_WAREHOUSE_USERNAME",
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full', 'id': 'id_new_session_name_input'})
    )

    def __init__(self, *args, **kwargs):
        user_warehouse = kwargs.pop('warehouse', None)
        super().__init__(*args, **kwargs)
        if user_warehouse:
            self.fields['active_session'].queryset = StockTakeSession.objects.filter(
                warehouse=user_warehouse,
                status__in=['PENDING', 'COMPLETED_BY_OPERATOR']
            ).order_by('-initiated_at')
            self.fields['active_session'].label_from_instance = lambda obj: f"{obj.name} (Started: {obj.initiated_at.strftime('%Y/%m/%d %H:%M')}) [{obj.get_status_display()}] "

    def clean(self):
        cleaned_data = super().clean()
        active_session = cleaned_data.get('active_session')
        session_name = cleaned_data.get('session_name')

        if not active_session and not session_name:
            raise forms.ValidationError(
                "Please either select an active session or provide a name for a new session.",
                code='no_selection_or_name'
            )
        return cleaned_data

class ErpStockCheckUploadForm(forms.Form):
    erp_file = forms.FileField(
        label="Select ERP Stock Report Excel File",
        help_text="Upload the .xlsx file from the ERP system (Sheet: 'Quantity grouped by product var').",
        widget=forms.ClearableFileInput(attrs={'accept': '.xlsx', 'class': 'file-input file-input-bordered file-input-primary w-full'})
    )
    session_name = forms.CharField(
        max_length=255,
        label="Stock Check Session Name",
        help_text="e.g., ERP Snapshot May 2025 - Main Warehouse",
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full'})
    )
    # Optional: Allow user to specify a warehouse if the check is for a single one
    # and the file might contain data for multiple.
    # If file is always for one, or warehouse is determined from file content, this might not be needed.
    warehouse = forms.ModelChoiceField(
    queryset=Warehouse.objects.all(),
    required=False,
    label="Specific Warehouse (optional)",
    help_text="Leave blank if the file covers multiple warehouses or if warehouse is in the file.",
    widget=forms.Select(attrs={'class': 'select select-bordered w-full'})
    )

class DefaultPickItemForm(forms.Form):
    """
    Form for a single row in the "Manage Default Picks" modal.
    Initially, only location_label is visible for search.
    Other fields are populated via JS after search.
    """
    inventory_batch_item_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    warehouse_product_id = forms.IntegerField(widget=forms.HiddenInput(), required=False) # To know which WP this default belongs to

    location_label_search = forms.CharField(
        max_length=100,
        required=False, # Not required for initial display of existing defaults
        label="Location Label",
        widget=forms.TextInput(attrs={
            'class': 'input input-sm input-bordered w-full default-pick-location-search',
            'placeholder': 'Search by Location Label'
        })
    )
    # These fields will be displayed as text, not inputs, after search
    product_name_display = forms.CharField(required=False, widget=forms.HiddenInput()) # Store for display
    batch_number_display = forms.CharField(required=False, widget=forms.HiddenInput())
    expiry_date_display = forms.CharField(required=False, widget=forms.HiddenInput())
    quantity_display = forms.CharField(required=False, widget=forms.HiddenInput())

    # Hidden field to mark for removal or to indicate it's a default
    is_default = forms.BooleanField(required=False, widget=forms.HiddenInput(), initial=True) # Assume initially it is/will be default

    def __init__(self, *args, **kwargs):
        self.warehouse = kwargs.pop('warehouse', None) # For filtering search by user's warehouse
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        # If an item is being actively set (e.g., not just displayed),
        # inventory_batch_item_id should be present.
        # This form is more for capturing the selected batch_item_id via JS.
        # Actual setting of pick_priority will happen in the view.
        return cleaned_data


class BaseDefaultPickItemFormSet(BaseFormSet):
    def __init__(self, *args, **kwargs):
        self.warehouse = kwargs.pop('warehouse', None)
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.warehouse = self.warehouse # Pass warehouse to individual forms

    def clean(self):
        if any(self.errors):
            return
        # Add any formset-level validation if needed (e.g., ensuring no duplicate locations for the same WP)
        # For now, the model's UniqueConstraint for pick_priority=0 handles uniqueness per WP.
        pass


DefaultPickItemFormSet = formset_factory(
    DefaultPickItemForm,
    formset=BaseDefaultPickItemFormSet,
    extra=1, # Start with one empty form for adding
    can_delete=True # Allows marking forms for deletion
)
