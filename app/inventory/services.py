import requests
import logging
import csv
import io



from django.db import transaction
from django.db.models import F, Q
from django.conf import settings
from django.utils import timezone

import datetime # If working with datetime.max.date

from .models import Product, InventoryBatchItem, StockTransaction, StockTakeSession, StockTakeItem
from warehouse.models import Warehouse, WarehouseProduct


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


def deduct_stock(warehouse_product, quantity_to_deduct, user, notes=""):
    """
    Deducts stock for a given warehouse product using FIFO logic based on expiry date.
    Creates stock transactions for the deductions.
    """
    with transaction.atomic():
        batches = InventoryBatchItem.objects.filter(
            warehouse_product=warehouse_product,
            quantity__gt=0
        ).order_by('expiry_date')

        remaining_quantity_to_deduct = quantity_to_deduct

        for batch in batches:
            if remaining_quantity_to_deduct <= 0:
                break

            quantity_to_deduct_from_batch = min(batch.quantity, remaining_quantity_to_deduct)

            batch.quantity -= quantity_to_deduct_from_batch
            batch.save()

            StockTransaction.objects.create(
                inventory_item=batch,
                transaction_type='DEDUCTION',
                quantity=quantity_to_deduct_from_batch,
                user=user,
                notes=notes
            )

            remaining_quantity_to_deduct -= quantity_to_deduct_from_batch

        if remaining_quantity_to_deduct > 0:
            # This check is important to ensure you don't proceed with an order if there's not enough stock.
            raise Exception(f"Not enough stock for {warehouse_product.product.name}. Cannot deduct {remaining_quantity_to_deduct} more units.")


@transaction.atomic
def create_stock_take_session_from_csv(file, user):
    """
    Creates a new StockTakeSession from a CSV file, correctly linking to
    WarehouseProduct and populating all related fields.
    """
    created_count = 0
    errors = []

    try:
        decoded_file = io.TextIOWrapper(file, encoding='utf-8-sig')
        reader = csv.DictReader(decoded_file)
        rows = list(reader)

        if not rows:
            errors.append("The uploaded file is empty or has no data rows.")
            return None, 0, errors

        # --- Get Warehouse and Create the Session ---
        first_row = rows[0]
        warehouse_name = first_row.get('warehouse_name', '').strip()
        if not warehouse_name:
            errors.append("Could not find a valid 'warehouse_name' in the file.")
            return None, 0, errors

        warehouse = Warehouse.objects.get(name__iexact=warehouse_name)

        session_name = f"Uploaded Stock Take - {warehouse.name} - {timezone.now().strftime('%Y-%m-%d %H:%M')}"
        session = StockTakeSession.objects.create(
            name=session_name,
            warehouse=warehouse,
            initiated_by=user,
            status='PENDING'
        )
        logger.info(f"Successfully created StockTakeSession ID: {session.id} for Warehouse: '{warehouse.name}'")

        # --- Process each row to create StockTakeItems ---
        for row_idx, row in enumerate(rows, start=2):
            try:
                # --- Step 1: Extract all data from the row ---
                product_sku = row.get('product_sku', '').strip()
                quantity_str = row.get('quantity', '').strip()
                batch_number = row.get('batch_number', '').strip()
                location_label = row.get('location_label', '').strip()
                expiry_date_str = row.get('expiry_date (YYYY-MM-DD)', '').strip()

                if not product_sku or not quantity_str:
                    continue # Skip blank lines

                # --- Step 2: Find the specific WarehouseProduct ---
                product = Product.objects.get(sku=product_sku)
                warehouse_product = WarehouseProduct.objects.get(product=product, warehouse=warehouse)

                # --- Step 3: Create the StockTakeItem with all fields ---
                StockTakeItem.objects.create(
                    session=session,
                    warehouse_product=warehouse_product, # Use the correct field
                    location_label_counted=location_label,
                    batch_number_counted=batch_number,
                    expiry_date_counted=datetime.datetime.strptime(expiry_date_str, '%Y-%m-%d').date() if expiry_date_str else None,
                    counted_quantity=int(quantity_str)
                )
                created_count += 1

            except Product.DoesNotExist:
                errors.append(f"Row {row_idx}: Product with SKU '{product_sku}' not found.")
            except WarehouseProduct.DoesNotExist:
                errors.append(f"Row {row_idx}: Product with SKU '{product_sku}' does not exist in Warehouse '{warehouse_name}'.")
            except (ValueError, TypeError, IndexError) as e:
                errors.append(f"Row {row_idx}: Skipped due to invalid data format. Error: {e}")

    except Warehouse.DoesNotExist:
        errors.append(f"Upload failed: Warehouse '{warehouse_name}' not found.")
        return None, 0, errors
    except Exception as e:
        errors.append(f"A fatal error occurred. Ensure the file is a clean 'CSV UTF-8'. Error: {e}")
        logger.error(f"Fatal error during stock take upload: {e}", exc_info=True)
        if 'session' in locals() and session.pk:
            session.delete()
        return None, 0, errors

    return session, created_count, errors
