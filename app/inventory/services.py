import requests
from django.conf import settings
from .models import InventoryBatchItem, OrderItem # If it's a model method
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

    Args:
        order_item: The OrderItem instance.
        quantity_needed: The integer quantity required for this order item.

    Returns:
        An InventoryBatchItem instance if a suitable batch is found, otherwise None.
    """
    if not order_item.warehouse_product:
        # Cannot suggest if the order item is not linked to a specific WarehouseProduct
        # (which links to a generic Product and a specific Warehouse)
        return None

    warehouse_product = order_item.warehouse_product
    today = timezone.now().date()

    # Define a base queryset for available stock
    # Available means quantity >= needed and not expired (or expiry is in the future/null)
    available_batches_base_qs = InventoryBatchItem.objects.filter(
        warehouse_product=warehouse_product,
        quantity__gte=quantity_needed
    ).exclude(
        expiry_date__isnull=False, expiry_date__lt=today # Exclude expired items
    )

    # 1. Try to find a 'Default Pick' (pick_priority = 0)
    default_pick_batch = available_batches_base_qs.filter(
        pick_priority=0
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first() # FEFO within default

    if default_pick_batch:
        return default_pick_batch

    # 2. If no suitable default, try to find a 'Secondary Pick' (pick_priority = 1)
    secondary_pick_batch = available_batches_base_qs.filter(
        pick_priority=1
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first() # FEFO within secondary

    if secondary_pick_batch:
        return secondary_pick_batch

    # 3. If no suitable default or secondary, fall back to general FEFO
    #    for batches with no priority (pick_priority is None) or any priority if partial fulfillment is considered later.
    #    The Meta.ordering of InventoryBatchItem already helps here if pick_priority is nulls_last.
    general_fefo_batch = available_batches_base_qs.filter(
        pick_priority__isnull=True # Only consider those not explicitly prioritized
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first() # FEFO for non-prioritized

    if general_fefo_batch:
        return general_fefo_batch

    # 4. OPTIONAL: If no single batch can fulfill the quantity, but you want to check if
    #    default or secondary picks have *any* stock (even if less than quantity_needed).
    #    This is for scenarios where you might show a "partial suggestion" or alert.
    #    For now, this function aims to find a single batch that can fulfill the entire quantity_needed.
    #
    #    If you wanted to check default/secondary even with insufficient stock:
    #    default_with_any_stock = InventoryBatchItem.objects.filter(
    #        warehouse_product=warehouse_product, pick_priority=0, quantity__gt=0
    #    ).exclude(expiry_date__isnull=False, expiry_date__lt=today)
    #    .order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first()
    #    if default_with_any_stock:
    #        # Handle this case - e.g. log it, or suggest partial. For now, we return None.
    #        pass


    # If no suitable batch is found by any criteria
    return None
