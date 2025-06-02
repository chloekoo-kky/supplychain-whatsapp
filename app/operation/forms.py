# app/operation/forms.py
from django import forms
from .models import Parcel, ParcelItem, OrderItem, Order
from inventory.models import InventoryBatchItem
import logging

logger = logging.getLogger(__name__)


class ExcelImportForm(forms.Form):
    excel_file = forms.FileField(
        label='Select Excel File',
        widget=forms.ClearableFileInput(attrs={'accept': '.xlsx,.xls'})
    )

    def clean_excel_file(self):
        file = self.cleaned_data.get('excel_file')
        if file:
            file_name = file.name.lower()
            if not (file_name.endswith('.xlsx') or file_name.endswith('.xls')):
                raise forms.ValidationError("Only .xlsx or .xls files are allowed.")
        return file

class ParcelForm(forms.ModelForm):
    class Meta:
        model = Parcel
        fields = ['courier_name', 'tracking_number', 'notes']
        widgets = {
            'courier_name': forms.TextInput(attrs={'class': 'input input-bordered input-sm w-full'}), # made sm
            'tracking_number': forms.TextInput(attrs={'class': 'input input-bordered input-sm w-full'}), # made sm
            'notes': forms.Textarea(attrs={'class': 'textarea textarea-bordered textarea-sm w-full', 'rows': 2}), # made sm
        }


class BaseParcelItemFormSet(forms.BaseInlineFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        # You can add custom fields to each form in the formset here if needed
        # For example, to display more info or provide specific widgets.

        # Make shipped_from_batch dependent on the order_item's warehouse_product's warehouse
        if form.instance and form.instance.order_item and form.instance.order_item.warehouse_product:
            warehouse = form.instance.order_item.warehouse_product.warehouse
            form.fields['shipped_from_batch'].queryset = InventoryBatchItem.objects.filter(
                warehouse_product__warehouse=warehouse,
                warehouse_product__product=form.instance.order_item.product, # Match product
                quantity__gt=0 # Only show batches with stock
            ).select_related('warehouse_product__product', 'warehouse_product__warehouse')
            form.fields['shipped_from_batch'].label_from_instance = lambda obj: f"{obj.batch_number} (Loc: {obj.location_label or 'N/A'}, Exp: {obj.expiry_date or 'N/A'}, Qty: {obj.quantity})"
        elif form.initial.get('order_item'):
            try:
                oi = OrderItem.objects.get(pk=form.initial.get('order_item'))
                if oi.warehouse_product:
                    warehouse = oi.warehouse_product.warehouse
                    form.fields['shipped_from_batch'].queryset = InventoryBatchItem.objects.filter(
                        warehouse_product__warehouse=warehouse,
                        warehouse_product__product=oi.product,
                        quantity__gt=0
                    ).select_related('warehouse_product__product', 'warehouse_product__warehouse')
                    form.fields['shipped_from_batch'].label_from_instance = lambda obj: f"{obj.batch_number} (Loc: {obj.location_label or 'N/A'}, Exp: {obj.expiry_date or 'N/A'}, Qty: {obj.quantity})"
            except OrderItem.DoesNotExist:
                 form.fields['shipped_from_batch'].queryset = InventoryBatchItem.objects.none()

        else:
            form.fields['shipped_from_batch'].queryset = InventoryBatchItem.objects.none()


ParcelItemFormSet = forms.inlineformset_factory(
    Parcel,
    ParcelItem,
    fields=('order_item', 'shipped_from_batch', 'quantity_shipped_in_this_parcel'),
    formset=BaseParcelItemFormSet,
    extra=0, # Start with no extra forms, user adds them as needed for an order's items
    can_delete=True,
    widgets={
        'order_item': forms.Select(attrs={'class': 'select select-bordered select-sm parcel-item-order-item-select'}),
        'shipped_from_batch': forms.Select(attrs={'class': 'select select-bordered select-sm parcel-item-batch-select'}),
        'quantity_shipped_in_this_parcel': forms.NumberInput(attrs={'class': 'input input-bordered input-sm parcel-item-qty-input', 'min': '0'}),
    }
)

class InitialParcelItemForm(forms.Form):
    order_item_id = forms.IntegerField(widget=forms.HiddenInput())
    # Display-only fields, pre-filled by the view based on OrderItem
    product_name = forms.CharField(
        widget=forms.TextInput(attrs={'readonly': True, 'class': 'input input-xs bg-gray-100 border-none w-full p-1 text-gray-700 leading-tight'})
    )
    sku = forms.CharField(
        widget=forms.TextInput(attrs={'readonly': True, 'class': 'input input-xs bg-gray-100 border-none w-full p-1 text-gray-700 leading-tight'})
    )
    quantity_to_pack = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={'class': 'input input-sm input-bordered w-20 pack-quantity-input text-center'})
    )
    # This hidden field will store the PK of the InventoryBatchItem chosen by the user from the dropdown.
    # Its value is set by JavaScript when the 'available_batches' dropdown changes.
    selected_batch_item_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)

    # This ChoiceField will be dynamically populated by JavaScript.
    # The 'initial' value for this field (the pre-selected batch) will be set by JS
    # using the 'selected_batch_item_id' that was passed in this form's initial data.
    available_batches = forms.ChoiceField(
        choices=[('', '--- Select Batch ---')], # Start with a placeholder
        required=False, # It becomes required if quantity_to_pack > 0 (validated in JS and view)
        widget=forms.Select(attrs={'class': 'select select-sm select-bordered w-full available-batches-select text-xs p-1 h-8 leading-tight'})
    )

    def __init__(self, *args, **kwargs):
        initial_data = kwargs.get('initial', {})
        super().__init__(*args, **kwargs)

        # Populate display fields and hidden order_item_id from initial data
        self.fields['order_item_id'].initial = initial_data.get('order_item_id')
        self.fields['product_name'].initial = initial_data.get('product_name', 'N/A')
        self.fields['sku'].initial = initial_data.get('sku', 'N/A')

        qty_pack_initial = initial_data.get('quantity_to_pack', 0)
        self.fields['quantity_to_pack'].initial = qty_pack_initial
        self.fields['quantity_to_pack'].widget.attrs['max'] = qty_pack_initial # Max initially is what's remaining

        # Store the initially suggested batch ID (from `get_suggested_batch_for_order_item`)
        # This will be used by JavaScript to pre-select an option in the `available_batches` dropdown
        # once the dropdown's choices are loaded via AJAX.
        suggested_batch_pk = initial_data.get('selected_batch_item_id')
        if suggested_batch_pk:
            self.fields['selected_batch_item_id'].initial = suggested_batch_pk
            # Note: We don't set self.fields['available_batches'].initial here directly with the ID,
            # as choices aren't populated yet. JS will handle making this ID the selected value in the dropdown.

    def clean(self):
        cleaned_data = super().clean()
        qty_to_pack = cleaned_data.get('quantity_to_pack')
        # 'selected_batch_item_id' is what gets submitted from the hidden input,
        # which JS should update from the 'available_batches' dropdown.
        selected_batch_id_on_submit = cleaned_data.get('selected_batch_item_id')

        if qty_to_pack is not None and qty_to_pack > 0:
            if not selected_batch_id_on_submit:
                self.add_error('available_batches', 'A batch must be selected if quantity to pack is greater than 0.')
            # Further validation (e.g., qty_to_pack vs selected_batch_item_id.quantity) happens in the view.
        elif qty_to_pack is None or qty_to_pack < 0: # quantity_to_pack should not be None if field is present
            self.add_error('quantity_to_pack', 'Quantity to pack must be zero or a positive number.')

        return cleaned_data

InitialParcelItemFormSet = forms.formset_factory(InitialParcelItemForm, extra=0)

class RemoveOrderItemForm(forms.Form):
    order_item_id = forms.IntegerField(widget=forms.HiddenInput()) # This should always have a value from initial
    product_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'readonly': True, 'class': 'input input-xs bg-gray-100 border-none w-full p-1 text-gray-700 leading-tight'})
    )
    sku = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'readonly': True, 'class': 'input input-xs bg-gray-100 border-none w-full p-1 text-gray-700 leading-tight'})
    )
    balance_quantity_to_pack_display = forms.IntegerField(
        label="Balance to Pack",
        required=False,
        widget=forms.NumberInput(attrs={'readonly': True, 'class': 'input input-sm input-bordered w-20 text-center bg-gray-100'})
    )
    quantity_to_remove = forms.IntegerField(
        min_value=0,
        label="Remove Qty",
        # This field IS required in the sense that a value (even 0) must be submitted.
        # However, making it required=False and handling None in clean method
        # can sometimes be more flexible if empty submission is possible.
        # For an IntegerField, an empty string in POST will cause "This field is required." if required=True.
        # Let's keep it required=True and ensure the HTML sends a value (like 0).
        required=True,
        widget=forms.NumberInput(attrs={'class': 'input input-sm input-bordered w-20 remove-quantity-input text-center'})
    )

    def __init__(self, *args, **kwargs):
        logger.debug(f"Form {kwargs.get('prefix', 'N/A')} __init__ called. Args: {args}, Kwargs: {kwargs}")

        direct_balance_kwarg = kwargs.pop('balance_quantity_to_pack', None)

        # Log initial before super
        initial_before_super = kwargs.get('initial', None)
        logger.debug(f"Form {kwargs.get('prefix', 'N/A')} - Initial data BEFORE super(): {initial_before_super}")

        super().__init__(*args, **kwargs)

        # Log self.initial AFTER super
        logger.debug(f"Form {self.prefix if self.prefix else 'N/A'} - self.initial AFTER super(): {self.initial}")

        if direct_balance_kwarg is not None:
            self.balance_quantity_to_pack = direct_balance_kwarg
            logger.debug(f"Form {self.prefix if self.prefix else 'N/A'}: Balance set from direct kwarg: {self.balance_quantity_to_pack}")
        elif self.initial and 'balance_quantity_to_pack' in self.initial:
            self.balance_quantity_to_pack = self.initial.get('balance_quantity_to_pack')
            logger.debug(f"Form {self.prefix if self.prefix else 'N/A'}: Balance set from self.initial: {self.balance_quantity_to_pack}")
        else:
            self.balance_quantity_to_pack = 0
            logger.debug(f"Form {self.prefix if self.prefix else 'N/A'}: Balance defaulted to 0. direct_balance_kwarg: {direct_balance_kwarg}, self.initial: {self.initial}")


        # Set initial values for display fields and widget attributes
        self.fields['balance_quantity_to_pack_display'].initial = self.balance_quantity_to_pack
        self.fields['quantity_to_remove'].widget.attrs['max'] = self.balance_quantity_to_pack

        # Ensure quantity_to_remove has an initial value of 0.
        # If the form is bound, self.initial is not used for field values, self.data is.
        # This line primarily affects unbound forms (GET request).
        if not self.is_bound:
            self.fields['quantity_to_remove'].initial = 0

        if self.initial:
            self.fields['product_name'].initial = self.initial.get('product_name', '')
            self.fields['sku'].initial = self.initial.get('sku', '')
            self.fields['order_item_id'].initial = self.initial.get('order_item_id')
            # logger.debug(f"Form {self.prefix}: Initial values set. order_item_id: {self.fields['order_item_id'].initial}, balance: {self.fields['balance_quantity_to_pack_display'].initial}")


    def clean_quantity_to_remove(self):
        qty_to_remove = self.cleaned_data.get('quantity_to_remove')

        # quantity_to_remove is now required=True, so it should not be None if the form is valid so far.
        # If it were None, form.is_valid() would be false, and this clean method might not even be called
        # or it might be called but the field would already have an error.
        if qty_to_remove is None:
            # This state implies the field was missing or empty and it's required.
            # Django's default IntegerField validation should catch this.
            # If we reach here and it's None, something is unexpected or field is not required.
            # Since we set required=True, this block might be redundant if Django handles it.
            # For safety, let's ensure it's 0 if it somehow becomes None post-field-validation.
            logger.warning(f"clean_quantity_to_remove: qty_to_remove is None, defaulting to 0. This is unexpected if field is required.")
            qty_to_remove = 0
            # raise forms.ValidationError("Quantity to remove is required.", code='required') # This would be if required=False

        if qty_to_remove < 0: # min_value=0 on field should catch this too.
            raise forms.ValidationError("Quantity to remove cannot be negative.")

        if qty_to_remove > self.balance_quantity_to_pack:
            raise forms.ValidationError(f"Cannot remove more than the balance ({self.balance_quantity_to_pack}).")

        return qty_to_remove

    def clean(self):
        cleaned_data = super().clean()
        # Ensure order_item_id is present (it's a hidden field but crucial)
        # Since it's IntegerField and required=True by default, Django should enforce its presence.
        # If it's missing, form.is_valid() will be false.
        if self.is_bound and 'order_item_id' not in cleaned_data: # Check after field validation
            # This scenario means the field was not in the POST data or was empty.
             logger.warning(f"Form {self.prefix}: 'order_item_id' missing from cleaned_data during clean(). Data: {self.data}")
             # self.add_error('order_item_id', 'This field is required.') # Redundant if field handles it
        return cleaned_data



# Formset for removing items
RemoveOrderItemFormSet = forms.formset_factory(RemoveOrderItemForm, extra=0)
