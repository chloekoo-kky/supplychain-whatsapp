# ===== warehouse/forms.py =====
from django import forms
from django.forms import inlineformset_factory

from warehouse.models import WarehouseProduct, PurchaseOrder, PurchaseOrderItem, WarehouseProduct



class WarehouseProductUpdateForm(forms.ModelForm):
    class Meta:
        model = WarehouseProduct
        fields = ['quantity']


class PurchaseOrderItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderItem
        fields = ['item', 'quantity', 'price']

    def __init__(self, *args, **kwargs):
        po = kwargs.pop('po', None)
        super().__init__(*args, **kwargs)

        if po:
            self.fields['item'].queryset = WarehouseProduct.objects.filter(product__supplier=po.supplier)


PurchaseOrderItemFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderItem,
    form=PurchaseOrderItemForm,
    extra=1,
    can_delete=True
)


class WarehouseProductDetailForm(forms.ModelForm):
    """
    A form for warehouse operators to edit warehouse-specific product details.
    """
    suggested_selling_price = forms.DecimalField(
        required=False,
        label="Suggested Selling Price",
        widget=forms.NumberInput(attrs={'class': 'input input-bordered w-full'})
    )

    class Meta:
        model = WarehouseProduct
        fields = [
            'photo',
            'length', 'width', 'height',
            'max_ship_qty_a', 'max_ship_qty_b'
        ]
        labels = {
            'max_ship_qty_a': 'Condition A (2 Big Ice Bricks)',
            'max_ship_qty_b': 'Condition B (2 Big Ice Bricks + 1 Small Ice Brick)',
        }
        widgets = {
            'photo': forms.ClearableFileInput(attrs={
                'class': 'file-input file-input-bordered w-full max-w-xs'
            }),
            'length': forms.NumberInput(attrs={
                'class': 'input input-bordered w-full', 'placeholder': 'e.g., 10.5'
            }),
            'width': forms.NumberInput(attrs={
                'class': 'input input-bordered w-full', 'placeholder': 'e.g., 8.0'
            }),
            'height': forms.NumberInput(attrs={
                'class': 'input input-bordered w-full', 'placeholder': 'e.g., 5.2'
            }),
            'max_ship_qty_a': forms.NumberInput(attrs={
                'class': 'input input-bordered w-full', 'placeholder': 'e.g., 100'
            }),
            'max_ship_qty_b': forms.NumberInput(attrs={
                'class': 'input input-bordered w-full', 'placeholder': 'e.g., 50'
            }),
        }

    def __init__(self, *args, **kwargs):
        """
        Initialize the form and set the initial value for the suggested selling price
        from the instance's selling_price or the related Product's current price.
        """
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            if self.instance.selling_price is not None:
                self.fields['suggested_selling_price'].initial = self.instance.selling_price
            elif self.instance.product:
                self.fields['suggested_selling_price'].initial = self.instance.product.price

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.selling_price = self.cleaned_data.get('suggested_selling_price')
        if commit:
            instance.save()
            self.save_m2m()
        return instance
