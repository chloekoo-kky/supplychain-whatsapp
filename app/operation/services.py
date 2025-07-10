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
    Fetches tracking details from the DHL API for a given tracking number.
    """
    api_key = getattr(settings, 'DHL_API_KEY', None)
    if not api_key:
        logger.error("DHL_API_KEY is not configured in settings.")
        return None # Return None on error

    url = f"https://api-eu.dhl.com/track/shipments"
    headers = {"DHL-API-Key": api_key}
    params = {"trackingNumber": tracking_number, "service": "express"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        shipments = data.get('shipments', [])
        if not shipments:
            return []

        events = shipments[0].get('events', [])
        parsed_events = []
        for event in events:
            timestamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
            parsed_events.append({
                'timestamp': timestamp,
                'description': event.get('description', 'No description'),
                'location': event.get('location', {}).get('address', {}).get('addressLocality', ''),
                'event_id': event.get('id', str(timestamp.timestamp()))
            })
        return parsed_events

    except requests.RequestException as e:
        logger.error(f"Error fetching DHL tracking for {tracking_number}: {e}")
        return None
    except (KeyError, TypeError) as e:
        logger.error(f"Error parsing DHL response for {tracking_number}: {e}")
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


def get_ups_tracking_details(tracking_number):
    """
    Fetches tracking details from the UPS API PRODUCTION environment.
    """
    logger.info(f"Attempting to fetch UPS tracking details for: {tracking_number}")
    access_token = get_ups_access_token()
    if not access_token:
        logger.error("Aborting UPS tracking fetch due to missing access token.")
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

        # *** THE FIX: Iterate through the list of packages ***
        packages = shipment.get('package', [])

        if not packages:
             logger.warning(f"No package information found in UPS response for {tracking_number}.")
             return []

        parsed_events = []
        for package in packages:
            activities = package.get('activity', [])
            for event in activities:
                date_str = event.get('date')
                time_str = event.get('time')
                timestamp = None
                if date_str and time_str:
                    try:
                        timestamp = timezone.make_aware(
                            datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                        )
                    except ValueError:
                        logger.warning(f"Could not parse UPS timestamp: {date_str} {time_str}")
                        timestamp = timezone.now()

                location_info = event.get('location', {}).get('address', {})
                location_str = f"{location_info.get('city', '')}, {location_info.get('stateProvince', '')} {location_info.get('countryCode', '')}".strip(', ')
                description = event.get('status', {}).get('description', 'No description')

                event_unique_id = f"{tracking_number}-{timestamp.isoformat() if timestamp else ''}-{description}"

                parsed_events.append({
                    'timestamp': timestamp,
                    'description': description,
                    'location': location_str,
                    'event_id': event_unique_id
                })

        return sorted(parsed_events, key=lambda x: x['timestamp'] or timezone.make_aware(datetime.min, timezone.utc), reverse=True)

    except requests.RequestException as e:
        logger.error(f"Error fetching UPS tracking for {tracking_number}: {e}")
        if e.response is not None:
            logger.error(f"UPS API Response Content: {e.response.text}")
        return None
    except (KeyError, TypeError, IndexError) as e:
        logger.error(f"Error parsing UPS response for {tracking_number}: {e}", exc_info=True)
        return None


def update_parcel_tracking_from_api(parcel: Parcel) -> (bool, str):
    """
    Fetches tracking data, updates logs, and intelligently updates the
    parcel's status based on a set of rules.
    """
    if not parcel.tracking_number:
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
        return False, f"No API handler for courier: {parcel.courier_company.name if parcel.courier_company else 'Unknown'}."

    if events is None:
        return False, "API call failed. Could not retrieve tracking events."

    if not events:
        if parcel.status == 'IN_TRANSIT' and parcel.shipped_at and (timezone.now() - parcel.shipped_at > timezone.timedelta(days=20)):
            parcel.status = 'DELIVERY_FAILED'
            parcel.save(update_fields=['status'])
            return True, "No new events found, but status updated to DELIVERY FAILED (in transit > 20 days)."
        return True, "No new tracking events found from API."

    new_logs_created_count = 0
    # Events from UPS are already sorted newest to oldest, so we can rely on that
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

    latest_event_description = events[0]['description'].lower() if events else ""

    if 'return' in latest_event_description and parcel.status != 'RETURNED':
        parcel.status = 'RETURNED'
        parcel.save(update_fields=['status'])
        return True, f"Status updated to RETURNED based on event: '{events[0]['description']}'."

    if 'delivered' in latest_event_description and parcel.status != 'DELIVERED':
        parcel.status = 'DELIVERED'
        parcel.save(update_fields=['status'])
        return True, f"Status updated to DELIVERED based on event: '{events[0]['description']}'."

    if parcel.status == 'IN_TRANSIT' and parcel.shipped_at and (timezone.now() - parcel.shipped_at > timezone.timedelta(days=20)):
        parcel.status = 'DELIVERY_FAILED'
        parcel.save(update_fields=['status'])
        return True, "Status updated to DELIVERY FAILED (in transit > 20 days)."

    if parcel.status not in ['DELIVERED', 'RETURNED', 'DELIVERY_FAILED', 'CANCELLED']:
        if parcel.status != 'IN_TRANSIT':
            parcel.status = 'IN_TRANSIT'
            parcel.save(update_fields=['status'])
            return True, "Status updated to IN_TRANSIT."

    return True, f"{new_logs_created_count} new events added. Status remains '{parcel.get_status_display()}'."

def parse_invoice_file(invoice):
    """Orchestrator to detect and call the correct invoice parser."""
    try:
        file_content_bytes = invoice.invoice_file.read()
        invoice.invoice_file.seek(0)
        first_line = file_content_bytes.splitlines()[0].decode('utf-8-sig', errors='ignore').upper().replace('"', '')
    except Exception as e:
        logger.error(f"Could not read or decode invoice file header: {e}")
        return 0, [f"Could not read file: {e}"], 0, []

    if 'AIR WAYBILL NUMBER' in first_line:
        return parse_fedex_invoice(invoice)
    elif 'SHIPMENT NUMBER' in first_line and 'LINE TYPE' in first_line:
        return parse_dhl_invoice(invoice)
    elif first_line.startswith('2.1,0000F'):
        return parse_ups_invoice(invoice)
    else:
        logger.warning(f"Unsupported invoice file type for: {invoice.invoice_file.name}.")
        return 0, ["Unsupported file type or format."], 0, []


def parse_dhl_invoice(invoice):
    """
    Parses a DHL CSV.
    """
    logger.info("Starting DHL parsing process.")
    created_items, updated_items = 0, 0
    errors, success_messages = [], [] # Initialized success_messages
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
            # FIX: Delete placeholder on critical error
            invoice.delete()
            return 0, errors, 0, []

        required_keys = {'LINE TYPE', 'INVOICE NUMBER', 'INVOICE DATE', 'SHIPMENT NUMBER', 'TOTAL AMOUNT (INCL. VAT)', 'DHL SCALE WEIGHT (B)', 'DHL VOL WEIGHT (W)', 'WEIGHT (KG)', 'RECEIVERS STATE/PROVINCE', 'DEST NAME'}
        missing_keys = [key for key in required_keys if key not in column_map]
        if missing_keys:
            errors.append(f"Critical Error: Header is missing required columns: {', '.join(missing_keys)}")
            return 0, errors, 0, []

        line_type_col = column_map['LINE TYPE']
        shipment_rows = [row for row in all_rows[header_row_index + 1:] if len(row) > line_type_col and row[line_type_col].strip() == 'S']

        if not shipment_rows:
            errors.append("No valid shipment rows (Line Type 'S') found.")
            return 0, errors, 0, []

        first_shipment_row = shipment_rows[0]
        temp_invoice_number = first_shipment_row[column_map['INVOICE NUMBER']].strip()
        temp_invoice_date = datetime.strptime(first_shipment_row[column_map['INVOICE DATE']].strip(), "%Y%m%d").date()

        if CourierInvoice.objects.filter(invoice_number=temp_invoice_number).exclude(pk=invoice.pk).exists():
            errors.append(f"Error: Invoice '{temp_invoice_number}' has already been uploaded.")
            return 0, errors, 0, []

        with transaction.atomic():
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
                    receiver_state = row[column_map['RECEIVERS STATE/PROVINCE']].strip()
                    destination_name = row[column_map['DEST NAME']].strip()
                    charge_data = {'invoice_number': temp_invoice_number, 'cost': str(new_cost), 'date': temp_invoice_date.isoformat(), 'courier_company_name': invoice.courier_company.name}
                    parcel_link = Parcel.objects.filter(tracking_number=tracking_number).first()

                    item, created = CourierInvoiceItem.objects.get_or_create(
                        tracking_number=tracking_number,
                        defaults={
                            'courier_invoice': invoice, 'actual_cost': new_cost,
                            'scale_weight': scale_weight, 'vol_weight': vol_weight,
                            'billed_weight': billed_weight, 'cost_history': [charge_data],
                            'receiver_state': receiver_state, 'destination_name': destination_name,
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
                        item.receiver_state = receiver_state
                        item.destination_name = destination_name
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
                try:
                    invoice_date = datetime.strptime(inv_date_str, "%d-%b-%y").date()
                except ValueError:
                    invoice_date = datetime.strptime(inv_date_str, "%d-%b-%Y").date()
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
                    try:
                        tracking_number = str(Decimal(tracking_number_raw).to_integral_value())
                    except:
                        tracking_number = tracking_number_raw

                    new_cost = Decimal(row[column_map['total_amount']].replace(',', ''))
                    charge_data = {'invoice_number': inv_num, 'cost': str(new_cost), 'date': inv_data['date'].isoformat(), 'courier_company_name': current_invoice.courier_company.name}
                    parcel_link = Parcel.objects.filter(tracking_number=tracking_number).first()

                    item, item_created = CourierInvoiceItem.objects.get_or_create(
                        tracking_number=tracking_number,
                        defaults={
                            'courier_invoice': current_invoice, 'actual_cost': new_cost,
                            'billed_weight': Decimal(row[column_map['billed_weight']].replace(',', '')) if row.get(column_map['billed_weight']) else None,
                            'receiver_state': row.get(column_map['receiver_state'], ''),
                            'destination_name': row.get(column_map['destination_name'], ''),
                            'parcel': parcel_link, 'cost_history': [charge_data]
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
                        'receiver_state': data['receiver_state'],
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

