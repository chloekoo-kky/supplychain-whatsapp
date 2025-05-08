# ===== warehouse/forms.py =====
from django import forms
from django.forms import inlineformset_factory

from warehouse.models import WarehouseProduct, PurchaseOrder, PurchaseOrderItem, WarehouseProduct



class WarehouseProductUpdateForm(forms.ModelForm):
    class Meta:
        model = WarehouseProduct
        fields = ['quantity', 'batch_number']


class PurchaseOrderItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderItem
        fields = ['item', 'quantity', 'price']

    def __init__(self, *args, **kwargs):
        po = kwargs.pop('po', None)
        super().__init__(*args, **kwargs)

    # 限定 item 为该 PO 的 supplier 所提供的 warehouse product
        if po:
            self.fields['item'].queryset = WarehouseProduct.objects.filter(product__supplier=po.supplier)


PurchaseOrderItemFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderItem,
    form=PurchaseOrderItemForm,
    extra=1,
    can_delete=True
)
