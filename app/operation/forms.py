# app/operation/forms.py
from django import forms
from .models import Parcel, ParcelItem, OrderItem, Order
from inventory.models import InventoryBatchItem

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

