# app/operation/forms.py
import random
from django import forms
from decimal import Decimal
from django.utils import timezone

from django.forms import inlineformset_factory, NumberInput
from .models import (Parcel,
                     ParcelItem,
                     OrderItem,
                     Order,
                     CustomsDeclaration,
                     CourierCompany,
                     PackagingType,
                     PackagingTypeMaterialComponent,
                     CourierInvoice,
                     CourierInvoiceItem)
from inventory.models import InventoryBatchItem, PackagingMaterial
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


class BaseParcelItemFormSet(forms.BaseInlineFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        if form.instance and form.instance.order_item and form.instance.order_item.warehouse_product:
            warehouse = form.instance.order_item.warehouse_product.warehouse
            form.fields['shipped_from_batch'].queryset = InventoryBatchItem.objects.filter(
                warehouse_product__warehouse=warehouse,
                warehouse_product__product=form.instance.order_item.product,
                quantity__gt=0
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
    extra=0,
    can_delete=True,
    widgets={
        'order_item': forms.Select(attrs={'class': 'select select-bordered select-sm parcel-item-order-item-select'}),
        'shipped_from_batch': forms.Select(attrs={'class': 'select select-bordered select-sm parcel-item-batch-select'}),
        'quantity_shipped_in_this_parcel': forms.NumberInput(attrs={'class': 'input input-bordered input-sm parcel-item-qty-input', 'min': '0'}),
    }
)

class InitialParcelItemForm(forms.Form):
    order_item_id = forms.IntegerField(widget=forms.HiddenInput())
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
    selected_batch_item_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    available_batches = forms.ChoiceField(
        choices=[('', '--- Select Batch ---')],
        required=False,
        widget=forms.Select(attrs={'class': 'select select-sm select-bordered w-full available-batches-select text-xs p-1 h-8 leading-tight'})
    )

    def __init__(self, *args, **kwargs):
        initial_data = kwargs.get('initial', {})
        super().__init__(*args, **kwargs)
        self.fields['order_item_id'].initial = initial_data.get('order_item_id')
        self.fields['product_name'].initial = initial_data.get('product_name', 'N/A')
        self.fields['sku'].initial = initial_data.get('sku', 'N/A')
        qty_pack_initial = initial_data.get('quantity_to_pack', 0)
        self.fields['quantity_to_pack'].initial = qty_pack_initial
        self.fields['quantity_to_pack'].widget.attrs['max'] = qty_pack_initial
        suggested_batch_pk = initial_data.get('selected_batch_item_id')
        if suggested_batch_pk:
            self.fields['selected_batch_item_id'].initial = suggested_batch_pk

    def clean(self):
        cleaned_data = super().clean()
        qty_to_pack = cleaned_data.get('quantity_to_pack')
        selected_batch_id_on_submit = cleaned_data.get('selected_batch_item_id')
        if qty_to_pack is not None and qty_to_pack > 0:
            if not selected_batch_id_on_submit:
                self.add_error('available_batches', 'A batch must be selected if quantity to pack is greater than 0.')
        elif qty_to_pack is None or qty_to_pack < 0:
            self.add_error('quantity_to_pack', 'Quantity to pack must be zero or a positive number.')
        return cleaned_data

InitialParcelItemFormSet = forms.formset_factory(InitialParcelItemForm, extra=0)

class RemoveOrderItemForm(forms.Form):
    order_item_id = forms.IntegerField(widget=forms.HiddenInput())
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
        required=True,
        widget=forms.NumberInput(attrs={'class': 'input input-sm input-bordered w-20 remove-quantity-input text-center'})
    )

    def __init__(self, *args, **kwargs):
        direct_balance_kwarg = kwargs.pop('balance_quantity_to_pack', None)
        super().__init__(*args, **kwargs)
        if direct_balance_kwarg is not None:
            self.balance_quantity_to_pack = direct_balance_kwarg
        elif self.initial and 'balance_quantity_to_pack' in self.initial:
            self.balance_quantity_to_pack = self.initial.get('balance_quantity_to_pack')
        else:
            self.balance_quantity_to_pack = 0
        self.fields['balance_quantity_to_pack_display'].initial = self.balance_quantity_to_pack
        self.fields['quantity_to_remove'].widget.attrs['max'] = self.balance_quantity_to_pack
        if not self.is_bound:
            self.fields['quantity_to_remove'].initial = 0
        if self.initial:
            self.fields['product_name'].initial = self.initial.get('product_name', '')
            self.fields['sku'].initial = self.initial.get('sku', '')
            self.fields['order_item_id'].initial = self.initial.get('order_item_id')

    def clean_quantity_to_remove(self):
        qty_to_remove = self.cleaned_data.get('quantity_to_remove')
        if qty_to_remove is None:
            qty_to_remove = 0
        if qty_to_remove < 0:
            raise forms.ValidationError("Quantity to remove cannot be negative.")
        if qty_to_remove > self.balance_quantity_to_pack:
            raise forms.ValidationError(f"Cannot remove more than the balance ({self.balance_quantity_to_pack}).")
        return qty_to_remove

    def clean(self):
        cleaned_data = super().clean()
        if self.is_bound and 'order_item_id' not in cleaned_data:
             logger.warning(f"Form {self.prefix}: 'order_item_id' missing from cleaned_data during clean(). Data: {self.data}")
        return cleaned_data

RemoveOrderItemFormSet = forms.formset_factory(RemoveOrderItemForm, extra=0)


# New Forms for Parcel Customs Details
class ParcelCustomsDetailForm(forms.ModelForm):
    dimensional_weight_kg = forms.CharField(widget=forms.HiddenInput(attrs={'id': 'id_dimensional_weight_kg'}), required=True)

    customs_declaration = forms.ModelChoiceField(
        queryset=CustomsDeclaration.objects.none(),
        widget=forms.RadioSelect,
        required=False,
        empty_label=None,
        label="Choose a Customs Description for this Parcel"
    )
    length = forms.IntegerField(
        required=True,
        label="Length (cm)",
        widget=forms.NumberInput(attrs={
            'class': 'input input-sm input-bordered w-1/5 dimensional-weight-input',
            'placeholder': 'L (cm)',
            'id': 'id_parcel_length_form'
        })
    )
    weight = forms.DecimalField(
        required=True,
        label="Parcel Actual Weight (kg)",
        widget=forms.NumberInput(attrs={
            'class': 'input input-bordered input-sm w-full',
            'placeholder': 'e.g., 1.25',
            'step': '0.1'
        })
    )
    width = forms.IntegerField(
        required=True,
        label="Width (cm)",
        widget=forms.NumberInput(attrs={
            'class': 'input input-sm input-bordered w-1/5 dimensional-weight-input',
            'placeholder': 'W (cm)',
            'id': 'id_parcel_width_form'
        })
    )
    height = forms.IntegerField(
        required=True,
        label="Height (cm)",
        widget=forms.NumberInput(attrs={
            'class': 'input input-sm input-bordered w-1/5 dimensional-weight-input',
            'placeholder': 'H (cm)',
            'id': 'id_parcel_height_form'
        })
    )
    declared_value_myr = forms.DecimalField(
        required=False,
        label="Total Declared Value (MYR)",
        widget=forms.NumberInput(attrs={
            'class': 'input input-bordered input-sm w-full bg-base-200', # Style as read-only
            'readonly': 'readonly',
            'step': '0.01',
            'id': 'id_declared_value_myr' # Add ID for JavaScript
        })
    )


    class Meta:
        model = Parcel
        fields = ['packaging_type', 'weight', 'length', 'width', 'height', 'customs_declaration', 'declared_value']

        widgets = {
            'packaging_type': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'weight': forms.NumberInput(attrs={'class': 'input input-bordered input-sm w-full', 'placeholder': 'e.g., 1.25', 'step': '0.1'}),
            'declared_value': forms.NumberInput(attrs={'class': 'input input-bordered input-sm w-full', 'placeholder': 'e.g., 45.90', 'step': '1', 'id': 'id_declared_value_usd'}),
        }

        labels = {
            'packaging_type': 'Packaging Type',
            'weight': 'Parcel Actual Weight (kg)',
            'customs_declaration': 'Customs Description',
            'declared_value': 'Total Declared Value (USD)',
        }

    def __init__(self, *args, **kwargs):
        declarations_queryset = kwargs.pop('declarations_queryset', None)
        super().__init__(*args, **kwargs)

        if declarations_queryset is not None:
            self.fields['customs_declaration'].queryset = declarations_queryset

            if self.instance and self.instance.customs_declaration:
                self.initial['customs_declaration'] = self.instance.customs_declaration.pk

        if self.instance and self.instance.pk:
            # Ensure existing dimension values are displayed as integers
            if self.instance.length is not None:
                self.initial['length'] = int(self.instance.length)
            if self.instance.width is not None:
                self.initial['width'] = int(self.instance.width)
            if self.instance.height is not None:
                self.initial['height'] = int(self.instance.height)

            # --- Logic for default values based on environment type ---
            effective_env_type = None
            if self.instance.packaging_type and self.instance.packaging_type.environment_type in ['COLD', 'AMBIENT']:
                effective_env_type = self.instance.packaging_type.environment_type
            elif self.instance.order.is_cold_chain:
                effective_env_type = 'COLD'
            else:
                effective_env_type = 'AMBIENT'

            # Set default weight & dimensions for cold chain if not already set
            if effective_env_type == 'COLD':
                if self.instance.weight is None or self.instance.weight <= 0:
                    self.initial['weight'] = 4.5
                if self.instance.length is None or self.instance.length <= 0:
                    self.initial['length'] = 30
                if self.instance.width is None or self.instance.width <= 0:
                    self.initial['width'] = 30
                if self.instance.height is None or self.instance.height <= 0:
                    self.initial['height'] = 25

            # --- ADD THIS BLOCK FOR DEFAULT DECLARED VALUE ---
            # Set default declared value if not already set
            if self.instance.declared_value is None or self.instance.declared_value <= 0:
                if effective_env_type == 'COLD':
                    self.initial['declared_value'] = random.randint(100, 150)
                elif effective_env_type == 'AMBIENT':
                    self.initial['declared_value'] = random.randint(80, 120)

            usd_value = self.initial.get('declared_value')
            if usd_value:
                try:
                    # Calculate MYR value and format to 2 decimal places
                    myr_value = Decimal(str(usd_value)) * Decimal('4.3')
                    self.initial['declared_value_myr'] = myr_value.quantize(Decimal('0.01'))
                except Exception:
                    # In case of an error, leave it blank
                    self.initial['declared_value_myr'] = None




class ParcelItemCustomsDetailForm(forms.ModelForm):
    # Display fields (read-only)
    product_name_display = forms.CharField(
        label="Product",
        required=False,
        widget=forms.TextInput(attrs={'readonly': True, 'class': 'input input-sm bg-gray-100 border-none p-1 text-gray-700 inline-block w-[250px]'})
    )
    sku_display = forms.CharField(
        label="SKU",
        required=False,
        widget=forms.TextInput(attrs={'readonly': True, 'class': 'input input-sm bg-gray-100 border-none p-1 text-gray-700 leading-tight inline-block w-[50px]'})
    )
    quantity_shipped_display = forms.IntegerField(label="Qty Shipped", required=False, widget=forms.NumberInput(attrs={'readonly': True, 'class': 'input input-sm bg-gray-100 border-none w-full p-1 text-gray-700 text-center'}))


    class Meta:
        model = ParcelItem
        fields = ['customs_description', 'declared_value'] # Editable fields
        widgets = {
            'customs_description': forms.TextInput(attrs={'class': 'input input-bordered input-xs w-full', 'placeholder': 'Item specific description'}),
            'declared_value': forms.NumberInput(attrs={'class': 'input input-bordered input-xs w-full', 'placeholder': 'Value', 'step': '1'}),
        }
        labels = {
            'customs_description': 'Item Customs Desc.',
            'declared_value': 'Item Decl. Value',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['product_name_display'].initial = self.instance.order_item.product.name if self.instance.order_item and self.instance.order_item.product else "N/A"
            self.fields['sku_display'].initial = self.instance.order_item.product.sku if self.instance.order_item and self.instance.order_item.product else "N/A"
            self.fields['quantity_shipped_display'].initial = self.instance.quantity_shipped_in_this_parcel


ParcelItemCustomsDetailFormSet = forms.inlineformset_factory(
    Parcel,
    ParcelItem,
    form=ParcelItemCustomsDetailForm,
    fields=['customs_description', 'declared_value'], # Only editable fields
    extra=0, # Don't show extra forms for adding new ParcelItems here
    can_delete=False # Don't allow deleting ParcelItems from this formset
)


class CustomsDeclarationForm(forms.ModelForm):
    courier_companies = forms.ModelMultipleChoiceField(
        queryset=CourierCompany.objects.filter(is_active=True).order_by('name'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Courier Companies",
        help_text="Select couriers this applies to. Leave blank if generic for all."
    )

    class Meta:
        model = CustomsDeclaration
        fields = [
            'description', 'hs_code',
            'courier_companies',
            'applies_to_ambient', 'applies_to_cold_chain', 'applies_to_mix',
            'notes'
        ]
        widgets = {
            'description': forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 3, 'placeholder': 'Detailed description of the goods'}),
            'hs_code': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'e.g., 8517.12.00'}),
            'notes': forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 2, 'placeholder': 'Optional notes or internal references'}),
            'applies_to_ambient': forms.CheckboxInput(attrs={'class': 'checkbox'}),
            'applies_to_cold_chain': forms.CheckboxInput(attrs={'class': 'checkbox'}),
            'applies_to_mix': forms.CheckboxInput(attrs={'class': 'checkbox'}),
        }


class PackagingTypeForm(forms.ModelForm):
    class Meta:
        model = PackagingType
        fields = ['name', 'type_code', 'description', 'environment_type', 'default_length_cm', 'default_width_cm', 'default_height_cm', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': "e.g., Standard Cold Box Setup A"}),
            'type_code': forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': "e.g., 'SCBA'"}),
            'description': forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 1}),
            'environment_type': forms.Select(attrs={'class': 'select select-bordered w-full'}),
            'default_length_cm': forms.NumberInput(attrs={'class': 'input input-bordered w-full'}),
            'default_width_cm': forms.NumberInput(attrs={'class': 'input input-bordered w-full'}),
            'default_height_cm': forms.NumberInput(attrs={'class': 'input input-bordered w-full'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'checkbox'}),
        }

class PackagingMaterialForm(forms.ModelForm):
    class Meta:
        model = PackagingMaterial
        fields = ['name', 'material_code', 'description', 'current_stock', 'reorder_level', 'supplier']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'material_code': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'description': forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 3}),
            'current_stock': forms.NumberInput(attrs={'class': 'input input-bordered w-full'}),
            'reorder_level': forms.NumberInput(attrs={'class': 'input input-bordered w-full'}),
            'supplier': forms.Select(attrs={'class': 'select select-bordered w-full'}),
        }

class PackagingMaterialForm(forms.ModelForm):
    class Meta:
        model = PackagingMaterial # Assuming this model is correctly imported or defined
        fields = ['name', 'material_code', 'description', 'current_stock', 'reorder_level', 'supplier']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'material_code': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'description': forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 3}),
            'current_stock': forms.NumberInput(attrs={'class': 'input input-bordered w-full'}),
            'reorder_level': forms.NumberInput(attrs={'class': 'input input-bordered w-full'}),
            'supplier': forms.Select(attrs={'class': 'select select-bordered w-full'}),
        }

PackagingTypeMaterialComponentFormSet = inlineformset_factory(
    PackagingType,
    PackagingTypeMaterialComponent,
    fields=('packaging_material', 'quantity'),
    extra=1,  # Show one empty form for adding a new material
    can_delete=True,  # Allow deleting existing components
    widgets={
        'packaging_material': forms.Select(attrs={'class': 'select select-bordered w-full'}),
        'quantity': NumberInput(attrs={'class': 'input input-bordered w-full', 'min': '0.01', 'step': '0.01'}), # Added min and step for quantity
    }
)

class AirwayBillForm(forms.ModelForm):
    """
    A specific form for capturing the Tracking ID and Estimated Cost
    in the Air Waybill modal.
    """
    class Meta:
        model = Parcel
        fields = ['tracking_number', 'estimated_cost']
        widgets = {
            'tracking_number': forms.TextInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': 'Enter tracking number'
            }),
            'estimated_cost': forms.NumberInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': 'e.g., 25.50',
                'step': '0.01'
            }),
        }
        labels = {
            'tracking_number': 'Tracking ID',
            'estimated_cost': 'Estimated Cost (MYR)',
        }

    def clean_tracking_number(self):
        """
        Validates that the tracking number is unique, excluding the current parcel instance.
        """
        tracking_number = self.cleaned_data.get('tracking_number')

        # We only validate if the user has entered a tracking number.
        if tracking_number:
            # Check for other parcels with the same tracking number.
            # We exclude the current parcel's pk so it doesn't fail validation against itself.
            if Parcel.objects.filter(tracking_number__iexact=tracking_number).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError(
                    "This tracking number is already in use by another parcel. Please enter a unique number."
                )
        return tracking_number

class CourierInvoiceForm(forms.ModelForm):
    """
    Form for uploading a new courier invoice.
    The user now only needs to select the courier and the file.
    """
    class Meta:
        model = CourierInvoice
        # MODIFIED: Removed invoice_number, invoice_date, and invoice_amount
        fields = ['courier_company', 'invoice_file']
        widgets = {
            'courier_company': forms.Select(attrs={'class': 'form-select'}),
            'invoice_file': forms.ClearableFileInput(attrs={'class': 'form-input'}),
        }

class CourierInvoiceItemForm(forms.ModelForm):
    """
    Form for an individual item on the courier invoice. (No changes here)
    """
    class Meta:
        model = CourierInvoiceItem
        fields = ['tracking_number', 'actual_cost']


class CourierInvoiceFilterForm(forms.Form):
    courier_company = forms.ModelChoiceField(
        queryset=CourierCompany.objects.filter(is_active=True),
        required=False,
        label="Courier",
        widget=forms.Select(attrs={'class': 'select select-bordered select-sm w-full'})
    )
    payment_status = forms.ChoiceField(
        choices=[('', 'All Statuses')] + CourierInvoice.PAYMENT_STATUS_CHOICES,
        required=False,
        label="Payment Status",
        widget=forms.Select(attrs={'class': 'select select-bordered select-sm w-full'})
    )
    q = forms.CharField(
        required=False,
        label="Search",
        widget=forms.TextInput(attrs={'class': 'input input-bordered input-sm w-full', 'placeholder': 'Invoice #'})
    )

class DisputeUpdateForm(forms.Form):
    update_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'input input-sm input-bordered w-full'}),
        required=False
    )
    remarks = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 1, 'placeholder': 'Required if date is set...'}),
        required=False
    )

    # --- MODIFICATION START ---
    def clean(self):
        """
        Custom validation to ensure that if one field in the "Add New Update"
        section is filled, the other is also required.
        """
        cleaned_data = super().clean()
        update_date = cleaned_data.get('update_date')
        remarks = cleaned_data.get('remarks')

        # Scenario 1: Date is provided, but remarks are missing.
        if update_date and not remarks:
            self.add_error('remarks', 'Remarks are required when an update date is provided.')

        # Scenario 2: Remarks are provided, but date is missing.
        if remarks and not update_date:
            self.add_error('update_date', 'An update date is required when remarks are provided.')

        return cleaned_data



class DisputeForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')

        if instance:
            # Always make the dispute date field readonly to prevent user changes.
            self.fields['dispute_date'].widget.attrs['readonly'] = True

            # --- MODIFICATION START ---
            if instance.dispute_date:
                # This is an EXISTING dispute. Add the grey background to show it's locked.
                self.fields['dispute_date'].widget.attrs['class'] += ' bg-gray-100 text-gray-500'
            else:
                # This is a NEW dispute. Set today's date but keep the white background.
                self.initial['dispute_date'] = timezone.now().date()
            # --- MODIFICATION END ---

            # FIX: Lock final amount if it exists
            if instance.final_amount_after_dispute is not None:
                self.fields['final_amount_after_dispute'].widget.attrs['disabled'] = True
                self.fields['final_amount_after_dispute'].widget.attrs['class'] += ' bg-gray-200'

            # FIX: Lock final amount date if it exists
            if instance.final_amount_date:
                self.fields['final_amount_date'].widget.attrs['disabled'] = True
                self.fields['final_amount_date'].widget.attrs['class'] += ' bg-gray-200'

    def clean_dispute_date(self):
        # The field is readonly, so we just return the value.
        # The old validation for future dates is no longer needed.
        return self.cleaned_data.get('dispute_date')


    # FIX: Add clean methods to preserve final amount fields when disabled
    def clean_final_amount_after_dispute(self):
        if self.instance and self.instance.pk and self.instance.final_amount_after_dispute is not None:
            return self.instance.final_amount_after_dispute
        return self.cleaned_data.get('final_amount_after_dispute')

    def clean_final_amount_date(self):
        if self.instance and self.instance.pk and self.instance.final_amount_date:
            return self.instance.final_amount_date
        return self.cleaned_data.get('final_amount_date')

    class Meta:
        model = CourierInvoiceItem
        fields = ['dispute_date', 'final_amount_after_dispute', 'final_amount_date']
        widgets = {
            'dispute_date': forms.DateInput(attrs={'type': 'date', 'class': 'input input-sm input-bordered w-full'}),
            'final_amount_after_dispute': forms.NumberInput(attrs={'class': 'input input-sm input-bordered w-full', 'placeholder': 'e.g., 150.25'}),
            'final_amount_date': forms.DateInput(attrs={'type': 'date', 'class': 'input input-sm input-bordered w-full'}),
        }


class ParcelEditForm(forms.ModelForm):
    """
    A comprehensive form for editing key parcel details in the modal,
    including the courier company.
    """
    def __init__(self, *args, **kwargs):
        # The declarations_queryset is passed in from the view
        declarations_queryset = kwargs.pop('declarations_queryset', None)
        super().__init__(*args, **kwargs)
        if declarations_queryset is not None:
            self.fields['customs_declaration'].queryset = declarations_queryset

    class Meta:
        model = Parcel
        fields = [
            'customs_declaration', 'weight', 'length',
            'width', 'height', 'dimensional_weight_kg', 'declared_value',
            'declared_value_myr'
        ]
        widgets = {
            'customs_declaration': forms.RadioSelect(),
            'weight': forms.NumberInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'e.g., 0.5'}),
            'length': forms.NumberInput(attrs={'class': 'input input-bordered w-full dimensional-input', 'placeholder': 'L (cm)'}),
            'width': forms.NumberInput(attrs={'class': 'input input-bordered w-full dimensional-input', 'placeholder': 'W (cm)'}),
            'height': forms.NumberInput(attrs={'class': 'input input-bordered w-full dimensional-input', 'placeholder': 'H (cm)'}),
            'dimensional_weight_kg': forms.HiddenInput(),
            'declared_value': forms.NumberInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'e.g., 10.00'}),
            'declared_value_myr': forms.NumberInput(attrs={'class': 'input input-bordered w-full', 'readonly': True}),
        }
