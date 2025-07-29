# app/operation/services.py

import requests
import csv
import openpyxl
import io
import logging
import base64 # Added missing import for UPS authentication

from django.conf import settings
from django.utils import timezone

from datetime import datetime, timedelta, timezone as dt_timezone
from django.core.cache import cache
from decimal import Decimal, InvalidOperation
from django.db import IntegrityError, transaction
from django.db.models import F

from .models import Parcel, ParcelTrackingLog, CourierInvoice, CourierInvoiceItem

logger = logging.getLogger(__name__)



def get_dhl_tracking_details(tracking_number):
    """
    Fetches tracking details from the DHL API for a given tracking number,
    using a more robust method for generating unique event IDs.
    """
    logger.info(f"[DHL Tracking] Starting process for tracking number: {tracking_number}")

    api_key = getattr(settings, 'DHL_API_KEY', None)
    if not api_key:
        logger.error("[DHL Tracking] CRITICAL: DHL_API_KEY is not configured.")
        return None

    url = f"https://api-eu.dhl.com/track/shipments"
    headers = {"DHL-API-Key": api_key}
    params = {
        "trackingNumber": tracking_number,
        "service": "express",
        "levelOfDetail": "ALL_EVENTS"
    }

    logger.info(f"[DHL Tracking] Sending request to URL: {url} with params: {params}")

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        logger.debug(f"[DHL Tracking] Raw Response Body for {tracking_number}: \n{response.text}")
        response.raise_for_status()

        data = response.json()
        shipments = data.get('shipments', [])
        if not shipments:
            logger.warning(f"[DHL Tracking] 'shipments' array is empty for {tracking_number}.")
            return []

        events = shipments[0].get('events', [])
        parsed_events = []
        for event in events:
            timestamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
            description = event.get('description', 'No description')

            # --- START OF THE FIX ---
            # Create a more unique fallback event_id by combining the timestamp
            # with the event description. This prevents duplicate IDs when
            # multiple events happen in the same second.
            fallback_id = f"{timestamp.timestamp()}-{description}"
            event_id = event.get('id', fallback_id)
            # --- END OF THE FIX ---

            parsed_events.append({
                'timestamp': timestamp,
                'description': description,
                'location': event.get('location', {}).get('address', {}).get('addressLocality', ''),
                'event_id': event_id
            })

        logger.info(f"[DHL Tracking] Successfully parsed {len(parsed_events)} events for {tracking_number}.")
        return parsed_events

    except requests.RequestException as e:
        logger.error(f"[DHL Tracking] Network Request Error for {tracking_number}: {e}")
        return None
    except Exception as e:
        logger.error(f"[DHL Tracking] An unexpected error occurred while processing {tracking_number}: {e}", exc_info=True)
        return None


def get_fedex_access_token():
    """
    Retrieves a FedEx API access token, utilizing cache.
    """
    token = cache.get('fedex_access_token')
    if token:
        logger.debug("Found FedEx access token in cache.")
        return token

    logger.info("No FedEx access token in cache, requesting a new one.")
    api_key = getattr(settings, 'FEDEX_API_KEY', None)
    secret_key = getattr(settings, 'FEDEX_SECRET_KEY', None)

    if not all([api_key, secret_key]):
        logger.error("FEDEX_API_KEY or FEDEX_SECRET_KEY are not configured.")
        return None

    auth_url = "https://apis.fedex.com/oauth/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key
    }

    try:
        response = requests.post(auth_url, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 3540)
        cache.set('fedex_access_token', access_token, timeout=expires_in - 60)
        logger.info("Successfully obtained and cached new FedEx access token.")
        return access_token
    except requests.RequestException as e:
        logger.error(f"Failed to get FedEx access token: {e}")
        if e.response is not None:
            logger.error(f"FedEx Auth API Response: {e.response.text}")
        return None

def get_fedex_tracking_details(tracking_number):
    """
    Fetches tracking details from the Fedex API for a given tracking number.
    """
    access_token = get_fedex_access_token()
    if not access_token:
        return None

    url = "https://apis.fedex.com/track/v1/trackingnumbers"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "includeDetailedScans": True,
        "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": tracking_number}}]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()

        data = response.json()
        track_results = data.get('output', {}).get('completeTrackResults', [])
        if not track_results or not track_results[0].get('trackResults'):
            return []

        events = track_results[0]['trackResults'][0].get('scanEvents', [])
        parsed_events = []
        for event in events:
            timestamp_str = event.get('date')
            if timestamp_str:
                dt_object = datetime.fromisoformat(timestamp_str)
                timestamp = timezone.make_aware(dt_object, timezone.utc) if dt_object.tzinfo is None else dt_object
            else:
                timestamp = timezone.now()

            event_unique_id = f"{tracking_number}-{event.get('date')}-{event.get('eventDescription')}"
            parsed_events.append({
                'timestamp': timestamp,
                'description': event.get('eventDescription', 'No description'),
                'location': event.get('scanLocation', {}).get('city', ''),
                'event_id': event_unique_id
            })
        return parsed_events

    except requests.RequestException as e:
        logger.error(f"Error fetching Fedex tracking for {tracking_number}: {e}")
        if e.response is not None:
            logger.error(f"FedEx API Response Content: {e.response.text}")
        return None
    except (KeyError, TypeError) as e:
        logger.error(f"Error parsing Fedex response for {tracking_number}: {e}")
        return None

def get_ups_access_token():
    """
    Retrieves a UPS API access token for the PRODUCTION environment.
    """
    token = cache.get('ups_access_token')
    if token:
        logger.debug("Found cached UPS API access token.")
        return token

    logger.info("No cached UPS token found, requesting a new one.")
    client_id = getattr(settings, 'UPS_CLIENT_ID', None)
    client_secret = getattr(settings, 'UPS_CLIENT_SECRET', None)

    if not client_id or not client_secret:
        logger.error("UPS_CLIENT_ID or UPS_CLIENT_SECRET are not configured.")
        return None

    # *** THE FIX: Use the production URL for authentication ***
    auth_url = "https://onlinetools.ups.com/security/v1/oauth/token"

    auth_string = f"{client_id}:{client_secret}"
    encoded_auth = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')

    headers = {
        "Authorization": f"Basic {encoded_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    payload = {"grant_type": "client_credentials"}

    try:
        response = requests.post(auth_url, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = int(token_data.get('expires_in', 14340))
        cache.set('ups_access_token', access_token, timeout=expires_in - 60)
        logger.info("Successfully obtained and cached new UPS access token.")
        return access_token
    except requests.RequestException as e:
        logger.error(f"Failed to get UPS access token: {e}")
        if e.response is not None:
            logger.error(f"UPS Auth API Response: {e.response.text}")
        return None

# In your operation/services.py file

def get_ups_tracking_details(tracking_number):
    """
    Fetches tracking details from the UPS API PRODUCTION environment.
    FIXED: Handles multiple timestamp formats and prevents using current time on parsing failure.
    """
    logger.info(f"[UPS Tracking] Attempting to fetch details for: {tracking_number}")
    access_token = get_ups_access_token()
    if not access_token:
        logger.error("[UPS Tracking] Aborting fetch due to missing access token.")
        return None

    url = f"https://onlinetools.ups.com/api/track/v1/details/{tracking_number}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "transId": str(timezone.now().timestamp()),
        "transactionSrc": "django-tracking-app"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        shipment = data.get('trackResponse', {}).get('shipment', [{}])[0]
        packages = shipment.get('package', [])

        if not packages:
             logger.warning(f"[UPS Tracking] No package information in response for {tracking_number}.")
             return []

        parsed_events = []
        for package in packages:
            activities = package.get('activity', [])
            for event in activities:
                date_str = event.get('date')
                time_str = event.get('time')
                timestamp = None # Default to None

                # --- START OF THE FIX ---
                if date_str and time_str:
                    # Try parsing with seconds first, then fall back to minutes
                    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M"):
                        try:
                            # Combine date and time and parse
                            dt_naive = datetime.strptime(f"{date_str}{time_str}", fmt)
                            # Make the datetime timezone-aware (assuming UTC)
                            timestamp = timezone.make_aware(dt_naive, dt_timezone.utc)
                            break # Success, exit the loop
                        except ValueError:
                            continue # Try the next format

                    if not timestamp:
                        # If all parsing formats failed, log the problematic data
                        logger.warning(f"[UPS Tracking] Could not parse timestamp for {tracking_number}. Date: '{date_str}', Time: '{time_str}'")
                # --- END OF THE FIX ---

                location_info = event.get('location', {}).get('address', {})
                location_str = f"{location_info.get('city', '')}, {location_info.get('stateProvince', '')} {location_info.get('countryCode', '')}".strip(', ')
                description = event.get('status', {}).get('description', 'No description')
                event_unique_id = f"{tracking_number}-{timestamp.isoformat() if timestamp else date_str}-{description}"

                parsed_events.append({
                    'timestamp': timestamp, # This will be the correct timestamp or None
                    'description': description,
                    'location': location_str,
                    'event_id': event_unique_id
                })

        # Sort events by timestamp, safely handling None values
        return sorted(parsed_events, key=lambda x: x['timestamp'] or datetime.min.replace(tzinfo=dt_timezone.utc), reverse=True)

    except requests.RequestException as e:
        logger.error(f"[UPS Tracking] Error fetching for {tracking_number}: {e}")
        if e.response is not None:
            logger.error(f"[UPS Tracking] API Response Content: {e.response.text}")
        return None
    except (KeyError, TypeError, IndexError) as e:
        logger.error(f"[UPS Tracking] Error parsing response for {tracking_number}: {e}", exc_info=True)
        return None

# In your operation/services.py file

def update_parcel_tracking_from_api(parcel: Parcel) -> (bool, str):
    """
    Fetches tracking data, updates logs, and intelligently updates the
    parcel's status and timestamps with detailed debug logging.
    """
    logger.info(f"\n--- [Tracking Update Debug] START: Tracing Parcel PK: {parcel.pk}, TN: {parcel.tracking_number} ---")
    if not parcel.tracking_number:
        logger.warning("[Tracking Update Debug] END: No tracking number found.")
        return False, "No tracking number."

    events = None
    courier_name = parcel.courier_company.name.lower() if parcel.courier_company else ""

    if 'dhl' in courier_name:
        events = get_dhl_tracking_details(parcel.tracking_number)
    elif 'fedex' in courier_name:
        events = get_fedex_tracking_details(parcel.tracking_number)
    elif 'ups' in courier_name:
        events = get_ups_tracking_details(parcel.tracking_number)
    else:
        logger.error(f"[Tracking Update Debug] END: No API handler for courier: {courier_name}")
        return False, f"No API handler for courier: {parcel.courier_company.name if parcel.courier_company else 'Unknown'}."

    if events is None:
        logger.error("[Tracking Update Debug] END: API call failed. Could not retrieve events.")
        return False, "API call failed. Could not retrieve tracking events."

    # --- Database Update Logic ---
    if not events:
        logger.info("[Tracking Update Debug] END: API returned no new tracking events.")
        if parcel.status == 'IN_TRANSIT' and parcel.shipped_at and (timezone.now() - parcel.shipped_at > timedelta(days=20)):
            parcel.status = 'DELIVERY_FAILED'
            parcel.save(update_fields=['status'])
            return True, "No new events, status updated to DELIVERY FAILED (in transit > 20 days)."
        return True, "No new tracking events found from API."

    logger.debug(f"[Tracking Update Debug] Received {len(events)} events from API (unsorted).")

    # Guarantee the sort order of events before processing.
    events.sort(key=lambda e: e.get('timestamp') or datetime.min.replace(tzinfo=dt_timezone.utc), reverse=True)
    logger.debug("[Tracking Update Debug] Events have been sorted by timestamp descending.")

    # Log the first and last events after sorting
    latest_event = events[0]
    earliest_event = events[-1]
    logger.debug(f"[Tracking Update Debug] Earliest Event: {earliest_event.get('timestamp')} - \"{earliest_event.get('description')}\"")
    logger.debug(f"[Tracking Update Debug] Latest Event:   {latest_event.get('timestamp')} - \"{latest_event.get('description')}\"")

    # First, save all new tracking logs to the database
    new_logs_created_count = 0
    for event_data in events:
        log, created = ParcelTrackingLog.objects.get_or_create(
            parcel=parcel,
            event_id=event_data.get('event_id'),
            defaults={
                'timestamp': event_data['timestamp'],
                'status_description': event_data['description'],
                'location': event_data.get('location'),
            }
        )
        if created:
            new_logs_created_count += 1

    # --- START OF THE FIX: Restructured Status and Timestamp Logic ---

    # Get the earliest and latest events from the full history
    latest_event = events[0]
    earliest_event = events[-1]
    latest_event_description = latest_event['description'].lower()

    fields_to_update = []

    logger.info(f"[Tracking Update Debug] Current Parcel Status: '{parcel.status}', Shipped At: {parcel.shipped_at}, Delivered At: {parcel.delivered_at}")

    # 1. Set shipped_at timestamp from the earliest event if it's not already set.
    # This is crucial for parcels traced for the first time after they've shipped.
    if not parcel.shipped_at:
        parcel.shipped_at = earliest_event['timestamp']
        fields_to_update.append('shipped_at')
        logger.info(f"[Tracking Update Debug] Staged 'shipped_at' for update: {parcel.shipped_at}")

    # 2. Determine the correct status and delivered_at timestamp based on the latest event.
    new_status = parcel.status
    if 'delivered' in latest_event_description:
        new_status = 'DELIVERED'
        # This is the critical check. Use the timestamp from the event.
        if latest_event.get('timestamp'):
            parcel.delivered_at = latest_event['timestamp']
            if 'delivered_at' not in fields_to_update:
                fields_to_update.append('delivered_at')
            logger.info(f"[Tracking Update Debug] Staged 'delivered_at' for update: {parcel.delivered_at}")
        else:
            logger.warning("[Tracking Update Debug] 'delivered' event found but it has no timestamp!")

    elif 'return' in latest_event_description:
        new_status = 'RETURNED_COURIER'
    elif parcel.status in ['READY_TO_SHIP', 'PREPARING_TO_PACK']:
        new_status = 'IN_TRANSIT'

    if parcel.status != new_status:
        parcel.status = new_status
        fields_to_update.append('status')
        logger.info(f"[Tracking Update Debug] Staged 'status' for update to: '{new_status}'")

    # 3. Save all collected changes to the database in one go.
    if fields_to_update:
        logger.info(f"[Tracking Update Debug] SAVING to DB. Fields to update: {fields_to_update}")
        parcel.save(update_fields=fields_to_update)
        message = f"Parcel {parcel.parcel_code_system} updated. New Status: '{parcel.get_status_display()}'."
    else:
        logger.info("[Tracking Update Debug] No changes to parcel status or timestamps.")
        message = f"{new_logs_created_count} new events added. Status remains '{parcel.get_status_display()}'."

    logger.info(f"--- [Tracking Update Debug] END: Process complete. ---")
    return True, message
    # --- END OF THE FIX ---

def parse_invoice_file(invoice):
    """
    Orchestrator to detect the courier from the invoice file, validate it
    against the selected company, and then call the correct parser.
    """
    try:
        file_content_bytes = invoice.invoice_file.read()
        invoice.invoice_file.seek(0)
        # Decode the first line to check for headers
        first_line = file_content_bytes.splitlines()[0].decode('utf-8-sig', errors='ignore').upper().replace('"', '')
    except Exception as e:
        logger.error(f"Could not read or decode invoice file header: {e}")
        return None, 0, [f"Could not read file: {e}"], 0, []

    # --- Courier Detection Logic ---
    detected_courier_name = None
    if 'AIR WAYBILL NUMBER' in first_line:
        detected_courier_name = 'FedEx'
    elif 'SHIPMENT NUMBER' in first_line and 'LINE TYPE' in first_line:
        detected_courier_name = 'DHL'
    elif first_line.startswith('2.1,0000F'):
        detected_courier_name = 'UPS'

    # --- Return the detected courier name for validation in the view ---
    if detected_courier_name:
        # Check if the detected courier matches the one selected in the form
        if detected_courier_name.lower() in invoice.courier_company.name.lower():
            if detected_courier_name == 'FedEx':
                return detected_courier_name, *parse_fedex_invoice(invoice)
            elif detected_courier_name == 'DHL':
                return detected_courier_name, *parse_dhl_invoice(invoice)
            elif detected_courier_name == 'UPS':
                return detected_courier_name, *parse_ups_invoice(invoice)
        else:
            # If it doesn't match, return an error
            error_message = f"Validation Error: The uploaded file appears to be a {detected_courier_name} invoice, but you selected {invoice.courier_company.name}."
            return None, 0, [error_message], 0, []

    logger.warning(f"Unsupported invoice file type for: {invoice.invoice_file.name}.")
    return None, 0, ["Unsupported file type or format."], 0, []

def parse_dhl_invoice(invoice):
    """
    Parses a DHL CSV.
    """
    logger.info("Starting DHL parsing process.")
    created_items, updated_items = 0, 0
    errors, success_messages = [], []
    total_invoice_amount = Decimal('0.0')

    try:
        try:
            file_content = invoice.invoice_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            invoice.invoice_file.seek(0)
            file_content = invoice.invoice_file.read().decode('latin-1')

        reader = csv.reader(io.StringIO(file_content))
        all_rows = list(reader)
        header_row_index = -1
        column_map = {}

        for i, row in enumerate(all_rows):
            if any(h.strip().upper() == 'SHIPMENT NUMBER' for h in row):
                header_row_index = i
                column_map = {h.strip().upper(): j for j, h in enumerate(row)}
                break

        if header_row_index == -1:
            errors.append("Critical Error: Could not find header row containing 'Shipment Number'.")
            invoice.delete()
            return 0, errors, 0, []

        required_keys = {
            'LINE TYPE', 'INVOICE NUMBER', 'INVOICE DATE', 'SHIPMENT NUMBER',
            'TOTAL AMOUNT (INCL. VAT)', 'DHL SCALE WEIGHT (B)', 'DHL VOL WEIGHT (W)',
            'WEIGHT (KG)', 'RECEIVERS STATE/PROVINCE', 'DEST NAME'
        }
        missing_keys = [key for key in required_keys if key not in column_map]
        if missing_keys:
            errors.append(f"Critical Error: Header is missing required columns: {', '.join(missing_keys)}")
            # No need to delete invoice here, it will be handled by the calling view if a critical error is returned
            return 0, errors, 0, []

        line_type_col = column_map['LINE TYPE']
        shipment_rows = [row for row in all_rows[header_row_index + 1:] if len(row) > line_type_col and row[line_type_col].strip() == 'S']

        if not shipment_rows:
            errors.append("No valid shipment rows (Line Type 'S') found.")
            # No need to delete invoice here if no rows are found, can be handled by the view
            return 0, errors, 0, []

        first_shipment_row = shipment_rows[0]
        temp_invoice_number = first_shipment_row[column_map['INVOICE NUMBER']].strip()
        temp_invoice_date = datetime.strptime(first_shipment_row[column_map['INVOICE DATE']].strip(), "%Y%m%d").date()

        # FIX: Delete the placeholder invoice if a duplicate is found
        if CourierInvoice.objects.filter(invoice_number=temp_invoice_number).exclude(pk=invoice.pk).exists():
            errors.append(f"Info: Invoice '{temp_invoice_number}' has already been uploaded.")
            invoice.delete()  # Delete the placeholder invoice object
            return 0, errors, 0, []

        with transaction.atomic():
            # ... (rest of the function remains the same)
            for row in shipment_rows:
                try:
                    tracking_number = row[column_map['SHIPMENT NUMBER']].strip()
                    if not tracking_number:
                        continue

                    new_cost = Decimal(row[column_map['TOTAL AMOUNT (INCL. VAT)']].strip())
                    total_invoice_amount += new_cost
                    scale_weight = Decimal(row[column_map['DHL SCALE WEIGHT (B)']].strip()) if row[column_map['DHL SCALE WEIGHT (B)']].strip() else None
                    vol_weight = Decimal(row[column_map['DHL VOL WEIGHT (W)']].strip()) if row[column_map['DHL VOL WEIGHT (W)']].strip() else None
                    billed_weight = Decimal(row[column_map['WEIGHT (KG)']].strip()) if row[column_map['WEIGHT (KG)']].strip() else None
                    state_abbr = row[column_map['RECEIVERS STATE/PROVINCE']].strip().upper()
                    city_name = row[column_map['DEST NAME']].strip()
                    receiver_state_full = STATE_MAP.get(state_abbr, state_abbr)
                    charge_data = {'invoice_number': temp_invoice_number, 'cost': str(new_cost), 'date': temp_invoice_date.isoformat(), 'courier_company_name': invoice.courier_company.name}
                    parcel_link = Parcel.objects.filter(tracking_number=tracking_number).first()

                    item, created = CourierInvoiceItem.objects.get_or_create(
                        tracking_number=tracking_number,
                        defaults={
                            'courier_invoice': invoice, 'actual_cost': new_cost,
                            'scale_weight': scale_weight, 'vol_weight': vol_weight,
                            'billed_weight': billed_weight, 'cost_history': [charge_data],
                            'receiver_state': receiver_state_full, 'destination_name': city_name,
                            'parcel': parcel_link,
                        }
                    )

                    if created:
                        created_items += 1
                    else:
                        item.actual_cost = F('actual_cost') + new_cost
                        item.courier_invoice = invoice
                        item.scale_weight = scale_weight
                        item.vol_weight = vol_weight
                        item.billed_weight = billed_weight
                        item.receiver_state = receiver_state_full
                        item.destination_name = city_name
                        if parcel_link and not item.parcel:
                            item.parcel = parcel_link
                        history = item.cost_history or []
                        history.append(charge_data)
                        item.cost_history = history
                        item.save()
                        updated_items += 1
                except (IndexError, ValueError, InvalidOperation, KeyError) as e:
                    errors.append(f"Skipped a shipment row due to a data error: {e}")
                    continue

            invoice.invoice_number = temp_invoice_number
            invoice.invoice_date = temp_invoice_date
            invoice.invoice_amount = total_invoice_amount
            invoice.save()
            success_messages.append(f"Successfully processed invoice {invoice.invoice_number} with total {total_invoice_amount}.")
            logger.info(success_messages[-1])

    except Exception as e:
        errors.append(f"A critical error occurred: {e}")
        logger.error(f"Critical file parsing error: {e}", exc_info=True)

    return created_items, errors, updated_items, success_messages

STATE_MAP = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
    'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
    'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
    'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire',
    'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina',
    'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania',
    'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee',
    'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming', 'DC': 'District of Columbia',
    'AS': 'American Samoa', 'GU': 'Guam', 'MP': 'Northern Mariana Islands', 'PR': 'Puerto Rico',
    'VI': 'U.S. Virgin Islands'
}

def parse_fedex_invoice(invoice):
    """
    Parses a FedEx CSV, handling multiple invoices and skipping duplicates.
    """
    logger.info("Starting multi-invoice-aware FedEx parsing process.")
    created_items_count, updated_items_count, processed_invoices_count = 0, 0, 0
    errors, success_messages = [], []

    try:
        file_content = invoice.invoice_file.read().decode('utf-8-sig', errors='ignore')
    except Exception as e:
        errors.append(f"Could not read file content: {e}")
        invoice.delete()
        return 0, errors, 0, []

    reader = csv.DictReader(io.StringIO(file_content))
    reader.fieldnames = [field.strip() for field in reader.fieldnames]

    header_possibilities = {
        'invoice_number': ['Invoice Number', 'FedEx Invoice Number'], 'invoice_date': ['Invoice Date'],
        'tracking_number': ['Air Waybill Number'], 'total_amount': ['Air Waybill Total Amount'],
        'receiver_state': ['Recipient Address State'], 'destination_name': ['Recipient Address City'],
        'billed_weight': ['Rated Weight Amount']
    }
    column_map, missing_cols = {}, []
    for key, names in header_possibilities.items():
        found_col = next((name for name in names if name in reader.fieldnames), None)
        if found_col:
            column_map[key] = found_col
        else:
            missing_cols.append(f"'{names[0]}'")

    if missing_cols:
        errors.append(f"Critical Error: Missing columns: {', '.join(missing_cols)}")
        invoice.delete()
        return 0, errors, 0, []

    invoices_data = {}
    for row in reader:
        try:
            inv_num = row.get(column_map['invoice_number'], '').strip()
            if not inv_num: continue
            if inv_num not in invoices_data:
                inv_date_str = row[column_map['invoice_date']].strip()

                # --- START: CORRECTED DATE PARSING LOGIC ---
                invoice_date = None
                # List of date formats to try in order
                possible_formats = [
                    '%d/%m/%Y',  # Handles '03/01/2025'
                    '%d-%b-%y',  # Handles '03-Jan-25'
                    '%d-%b-%Y',  # Handles '03-Jan-2025'
                    '%Y-%m-%d',  # Handles '2025-01-03'
                ]
                for fmt in possible_formats:
                    try:
                        invoice_date = datetime.strptime(inv_date_str, fmt).date()
                        break # If successful, stop trying other formats
                    except ValueError:
                        continue # If it fails, try the next format

                if not invoice_date:
                    # If date is still None after trying all formats, skip this row
                    errors.append(f"Skipped row: Could not parse date '{inv_date_str}' for invoice '{inv_num}'.")
                    continue

                invoices_data[inv_num] = {'date': invoice_date, 'rows': []}
            invoices_data[inv_num]['rows'].append(row)
        except (KeyError, ValueError) as e:
            errors.append(f"Skipped a row during grouping: {e}")
            continue

    if not invoices_data:
        invoice.delete()
        errors.append("No valid invoice data found.")
        return 0, errors, 0, []

    for inv_num, inv_data in invoices_data.items():
        try:
            with transaction.atomic():
                current_invoice, created = CourierInvoice.objects.get_or_create(
                    invoice_number=inv_num,
                    courier_company=invoice.courier_company,
                    defaults={
                        'invoice_date': inv_data['date'],
                        'invoice_amount': sum(Decimal(r[column_map['total_amount']].replace(',', '')) for r in inv_data['rows'] if r.get(column_map['total_amount'])),
                        'uploaded_by': invoice.uploaded_by, 'invoice_file': invoice.invoice_file
                    }
                )

                if not created:
                    errors.append(f"Info: Invoice '{inv_num}' already exists. Skipped.")

                    continue

                processed_invoices_count += 1
                for row in inv_data['rows']:
                    tracking_number_raw = row.get(column_map['tracking_number'], '').strip()
                    if not tracking_number_raw: continue

                    tracking_number = tracking_number_raw

                    new_cost = Decimal(row[column_map['total_amount']].replace(',', ''))
                    charge_data = {'invoice_number': inv_num, 'cost': str(new_cost), 'date': inv_data['date'].isoformat(), 'courier_company_name': current_invoice.courier_company.name}
                    parcel_link = Parcel.objects.filter(tracking_number=tracking_number).first()

                    state_abbr = row.get(column_map['receiver_state'], '').strip().upper()
                    # Use the map to get the full name, or fall back to the abbreviation if not found
                    receiver_state_full = STATE_MAP.get(state_abbr, state_abbr)

                    item, item_created = CourierInvoiceItem.objects.get_or_create(
                        tracking_number=tracking_number,
                        defaults={
                            'courier_invoice': current_invoice,
                            'actual_cost': new_cost,
                            'billed_weight': Decimal(row[column_map['billed_weight']].replace(',', '')) if row.get(column_map['billed_weight']) else None,
                            'receiver_state': receiver_state_full,  # ✅ Use the full state name
                            'destination_name': row.get(column_map['destination_name'], ''),
                            'parcel': parcel_link,
                            'cost_history': [charge_data]
                        }
                    )
                    if item_created:
                        created_items_count += 1
                    else:
                        item.actual_cost = F('actual_cost') + new_cost
                        item.courier_invoice = current_invoice
                        if parcel_link and not item.parcel:
                            item.parcel = parcel_link
                        history = item.cost_history or []
                        history.append(charge_data)
                        item.cost_history = history
                        item.save()
                        updated_items_count += 1
        except Exception as e:
            errors.append(f"Error on invoice '{inv_num}': {e}. Skipped.")
            continue

    invoice.delete()
    if processed_invoices_count > 0:
        success_messages.append(f"Successfully processed {processed_invoices_count} new invoice(s).")

    return created_items_count, errors, updated_items_count, success_messages

def parse_ups_invoice(invoice):
    """
    **FIXED**: Parses a UPS header-less CSV invoice file with updated column indices
    and robust error handling for decimal conversion.
    """
    logger.info("Starting UPS invoice parsing process with updated format.")
    errors, success_messages = [], []
    shipment_data = {}
    total_invoice_amount = Decimal('0.0')
    temp_invoice_number, temp_invoice_date = None, None

    try:
        file_content = invoice.invoice_file.read().decode('utf-8-sig')
        reader = csv.reader(io.StringIO(file_content))

        for i, row in enumerate(reader):
            row_num = i + 1
            if len(row) < 91: # Check against a column index that should exist
                logger.warning(f"Skipping row {row_num} in UPS file: not enough columns.")
                continue

            try:
                # --- Positional Column Mapping (Updated) ---
                tracking_number = row[13].strip()
                if not tracking_number:
                    continue

                if not temp_invoice_number:
                    temp_invoice_number = row[5].strip().lstrip('0')
                    temp_invoice_date = datetime.strptime(row[4].strip(), "%Y-%m-%d").date()

                    if CourierInvoice.objects.filter(invoice_number=temp_invoice_number).exclude(pk=invoice.pk).exists():
                        errors.append(f"Error: Invoice '{temp_invoice_number}' has already been uploaded.")
                        invoice.delete()
                        return 0, errors, 0, []

                # **FIX**: Use column 55 for charge amount and handle conversion errors.
                try:
                    charge_amount = Decimal(row[52].strip())
                except InvalidOperation:
                    logger.warning(f"Invalid charge amount on row {row_num}. Value was '{row[52]}'. Skipping charge.")
                    charge_amount = Decimal('0.0')

                if tracking_number not in shipment_data:
                    try:
                        billed_weight = Decimal(row[28].strip() or '0.0')
                    except InvalidOperation:
                        logger.warning(f"Invalid billed weight on row {row_num}. Value was '{row[28]}'. Defaulting to 0.")
                        billed_weight = Decimal('0.0')

                    shipment_data[tracking_number] = {
                        'total_cost': Decimal('0.0'),
                        'billed_weight': billed_weight,
                        'receiver_state': row[79].strip(),
                        'destination_name': row[78].strip(),
                        'charge_list': []
                    }

                # Summing up all charges for the tracking number
                shipment_data[tracking_number]['total_cost'] += charge_amount
                shipment_data[tracking_number]['charge_list'].append({
                    'description': row[49].strip(),
                    'amount': str(charge_amount)
                })
                total_invoice_amount += charge_amount

            except IndexError as e:
                errors.append(f"Skipped a row in UPS file due to a missing column: {e} on row {row_num}")
                continue
            except ValueError as e:
                errors.append(f"Skipped a row in UPS file due to a data value error: {e} on row {row_num}")
                continue


        if not temp_invoice_number:
            errors.append("Could not determine invoice number from UPS file.")
            invoice.delete()
            return 0, errors, 0, []

        created_items = 0
        updated_items = 0
        with transaction.atomic():
            for tracking, data in shipment_data.items():
                parcel_link = Parcel.objects.filter(tracking_number=tracking).first()
                state_abbr = data['receiver_state'].strip().upper()
                # Use the map to get the full name, or fall back to the abbreviation if not found
                receiver_state_full = STATE_MAP.get(state_abbr, state_abbr)

                charge_data = {
                    'invoice_number': temp_invoice_number,
                    'cost': str(data['total_cost']),
                    'date': temp_invoice_date.isoformat(),
                    'courier_company_name': invoice.courier_company.name,
                    'charges': data['charge_list']
                }

                item, created = CourierInvoiceItem.objects.get_or_create(
                    tracking_number=tracking,
                    defaults={
                        'courier_invoice': invoice,
                        'actual_cost': data['total_cost'],
                        'billed_weight': data['billed_weight'],
                        'receiver_state': receiver_state_full, # ✅ Use the full state name
                        'destination_name': data['destination_name'],
                        'parcel': parcel_link,
                        'cost_history': [charge_data],
                    }
                )

                if created:
                    created_items += 1
                else:
                    item.actual_cost = F('actual_cost') + data['total_cost']
                    item.courier_invoice = invoice
                    item.billed_weight = data['billed_weight']
                    item.receiver_state = data['receiver_state']
                    item.destination_name = data['destination_name']
                    if parcel_link and not item.parcel:
                        item.parcel = parcel_link

                    history = item.cost_history or []
                    history.append(charge_data)
                    item.cost_history = history
                    item.save()
                    updated_items += 1

            invoice.invoice_number = temp_invoice_number
            invoice.invoice_date = temp_invoice_date
            invoice.invoice_amount = total_invoice_amount
            invoice.save()
            success_messages.append(f"Successfully processed UPS invoice {invoice.invoice_number} with total {total_invoice_amount}.")
            logger.info(success_messages[-1])

    except Exception as e:
        errors.append(f"A critical error occurred during UPS file parsing: {e}")
        logger.error(f"Critical UPS file parsing error: {e}", exc_info=True)
        if invoice.pk and CourierInvoice.objects.filter(pk=invoice.pk).exists():
            invoice.delete()

    return created_items, errors, updated_items, success_messages

