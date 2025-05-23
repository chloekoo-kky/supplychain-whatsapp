import requests
from django.conf import settings
from .models import InventoryBatchItem
from inventory.models import InventoryBatchItem # If in a different app's service
from django.db.models import F, Q
from django.utils import timezone
import datetime # If working with datetime.max.date

def send_whatsapp_notification(to_number, message):
    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message}
    }
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"[WHATSAPP ERROR] {e}")

def get_suggested_batch_for_order_item(order_item, quantity_needed: int):
    """
    Finds the best available InventoryBatchItem for a given OrderItem and quantity.
    Priority: Default Pick (0), then Secondary Pick (1), then general FEFO.
    """
    if not order_item.warehouse_product:
        return None

    warehouse_product = order_item.warehouse_product
    today = timezone.now().date()

    # Base queryset for available stock (quantity >= needed, not expired)
    available_batches_base_qs = InventoryBatchItem.objects.filter(
        warehouse_product=warehouse_product,
        quantity__gte=quantity_needed # Ensure the batch has enough quantity
    ).exclude(
        expiry_date__isnull=False, expiry_date__lt=today
    )

    # 1. Try to find a 'Default Pick' (pick_priority = 0)
    default_pick_batch = available_batches_base_qs.filter(
        pick_priority=0
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first() # FEFO within default

    if default_pick_batch:
        # If default pick has enough quantity, return it.
        # The base query already filters for quantity__gte=quantity_needed
        return default_pick_batch

    # 2. If no suitable default (or default had insufficient stock and was filtered out by base_qs),
    #    try to find a 'Secondary Pick' (pick_priority = 1)
    secondary_pick_batch = available_batches_base_qs.filter(
        pick_priority=1
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first() # FEFO within secondary

    if secondary_pick_batch:
        # If secondary pick has enough quantity, return it.
        return secondary_pick_batch

    # 3. If no suitable default or secondary, fall back to general FEFO
    #    for batches with no priority (pick_priority is None).
    general_fefo_batch = available_batches_base_qs.filter(
        pick_priority__isnull=True # Only consider those not explicitly prioritized
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first() # FEFO for non-prioritized

    if general_fefo_batch:
        return general_fefo_batch

    # 4. If still no batch found that can fulfill the entire quantity_needed,
    #    check if default or secondary picks have *any* stock. This part is for
    #    potential partial allocation or alternative logic if fullfillment is not possible from one batch.
    #    This is a simplified check and might need more complex logic for actual partials.

    # Check Default Pick again, but this time for *any* stock (quantity > 0)
    default_pick_any_stock = InventoryBatchItem.objects.filter(
        warehouse_product=warehouse_product,
        pick_priority=0,
        quantity__gt=0 # Check for any stock
    ).exclude(expiry_date__isnull=False, expiry_date__lt=today) \
     .order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first()

    if default_pick_any_stock:
        # Default pick exists and has some stock, but not enough for the whole order_item.
        # Depending on business rules, you might return this for partial allocation,
        # or log it, or proceed to check secondary. For now, let's assume we prioritize it if available.
        # If you implement partial allocations, this is where you'd return it.
        # For now, the main queries above look for batches that can fulfill the *entire* quantity_needed.
        # This block can be enhanced if partial fulfillment from default/secondary is desired.
        # logger.info(f"Default pick {default_pick_any_stock.pk} has insufficient stock ({default_pick_any_stock.quantity}) for {quantity_needed} of {order_item.product.name}")
        pass # Fall through to check secondary or general FEFO if no single batch can fulfill

    # If no single batch can fulfill the quantity_needed by any of the prioritized methods.
    return None
