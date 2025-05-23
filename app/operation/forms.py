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
            'courier_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'tracking_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'notes': forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 3}),
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
    """
    A non-model form to represent an item to be packed.
    Used to dynamically build rows in the "Pack Order" modal.
    """
    order_item_id = forms.IntegerField(widget=forms.HiddenInput())
    product_name = forms.CharField(widget=forms.TextInput(attrs={'readonly': True, 'class': 'input input-sm bg-gray-100 border-none w-full'}))
    sku = forms.CharField(widget=forms.TextInput(attrs={'readonly': True, 'class': 'input input-sm bg-gray-100 border-none w-full'}))
    quantity_to_pack = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={'class': 'input input-sm input-bordered w-20 pack-quantity-input'})
    )
    # This field will be populated by JS/AJAX or a secondary select after batch is chosen
    selected_batch_item_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    # Add a select for available batches (will be populated/filtered by JS)
    available_batches = forms.ModelChoiceField(
        queryset=InventoryBatchItem.objects.none(), # Start with none, populate with JS
        required=False, # Batch might not be selected initially
        widget=forms.Select(attrs={'class': 'select select-sm select-bordered w-full available-batches-select'})
    )

    def __init__(self, *args, **kwargs):
        order_item_instance = kwargs.pop('order_item_instance', None)
        super().__init__(*args, **kwargs)
        if order_item_instance:
            self.fields['order_item_id'].initial = order_item_instance.pk
            self.fields['product_name'].initial = order_item_instance.product.name if order_item_instance.product else order_item_instance.erp_product_name
            self.fields['sku'].initial = order_item_instance.product.sku if order_item_instance.product else "N/A"
            # Set max for quantity_to_pack based on order_item.quantity_remaining_to_pack
            remaining_to_pack = order_item_instance.quantity_allocated - order_item_instance.quantity_packed
            self.fields['quantity_to_pack'].widget.attrs['max'] = remaining_to_pack
            self.fields['quantity_to_pack'].initial = remaining_to_pack # Default to packing the remaining amount

            # Populate available_batches queryset if warehouse_product exists
            if order_item_instance.suggested_batch_item:
                 self.fields['selected_batch_item_id'].initial = order_item_instance.suggested_batch_item_id
                 self.fields['available_batches'].queryset = InventoryBatchItem.objects.filter(
                    warehouse_product=order_item_instance.warehouse_product, quantity__gt=0
                 ).select_related('warehouse_product__product')
                 self.fields['available_batches'].initial = order_item_instance.suggested_batch_item_id

            elif order_item_instance.warehouse_product:
                self.fields['available_batches'].queryset = InventoryBatchItem.objects.filter(
                    warehouse_product=order_item_instance.warehouse_product, quantity__gt=0
                ).select_related('warehouse_product__product')
                self.fields['available_batches'].label_from_instance = lambda obj: f"B: {obj.batch_number or 'N/A'} L: {obj.location_label or 'N/A'} E: {obj.expiry_date or 'N/A'} (Qty: {obj.quantity})"
            else: # No specific warehouse product, might be problematic for batch selection
                self.fields['available_batches'].queryset = InventoryBatchItem.objects.none()


InitialParcelItemFormSet = forms.formset_factory(InitialParcelItemForm, extra=0)
