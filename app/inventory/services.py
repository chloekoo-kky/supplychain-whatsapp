import requests
import logging

from django.conf import settings
from .models import InventoryBatchItem
from inventory.models import InventoryBatchItem # If in a different app's service
from django.db.models import F, Q
from django.utils import timezone
import datetime # If working with datetime.max.date


logger = logging.getLogger(__name__)
logger.debug("Attempting to load inventory.services.py module...")


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
    logger.debug(f"get_suggested_batch_for_order_item called for OI ID: {order_item.id if order_item else 'None'}, Qty needed: {quantity_needed}")
    if not hasattr(order_item, 'warehouse_product') or not order_item.warehouse_product:
        logger.warning(f"OrderItem ID {order_item.id if order_item else 'Unknown'} has no linked warehouse_product. Cannot suggest batch.")
        return None

    warehouse_product = order_item.warehouse_product
    today = timezone.now().date()

    available_batches_base_qs = InventoryBatchItem.objects.filter(
        warehouse_product=warehouse_product,
        quantity__gte=quantity_needed
    ).exclude(
        expiry_date__isnull=False, expiry_date__lt=today
    )

    default_pick_batch = available_batches_base_qs.filter(
        pick_priority=0
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first()

    if default_pick_batch:
        logger.info(f"Found Default Pick Batch ID: {default_pick_batch.id} for OI ID: {order_item.id}")
        return default_pick_batch

    secondary_pick_batch = available_batches_base_qs.filter(
        pick_priority=1
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first()

    if secondary_pick_batch:
        logger.info(f"Found Secondary Pick Batch ID: {secondary_pick_batch.id} for OI ID: {order_item.id}")
        return secondary_pick_batch

    general_fefo_batch = available_batches_base_qs.filter(
        pick_priority__isnull=True
    ).order_by(F('expiry_date').asc(nulls_last=True), 'date_received').first()

    if general_fefo_batch:
        logger.info(f"Found General FEFO Batch ID: {general_fefo_batch.id} for OI ID: {order_item.id}")
        return general_fefo_batch

    logger.warning(f"No suitable batch found for OI ID: {order_item.id} needing quantity {quantity_needed}")
    return None

logger.info("inventory.services.py loaded and function 'get_suggested_batch_for_order_item' is defined.")
