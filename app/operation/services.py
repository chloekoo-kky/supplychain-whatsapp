# app/operation/services.py

import requests
from django.conf import settings
from django.utils import timezone
from datetime import datetime
import logging
from django.core.cache import cache

# Add these imports at the top
from .models import Parcel, ParcelTrackingLog

logger = logging.getLogger(__name__)


def get_dhl_tracking_details(tracking_number):
    """
    Fetches tracking details from the DHL API for a given tracking number.
    (This is your existing, awesome function)
    """
    # ... (your existing function code remains here) ...
    # Store your API key securely in your settings.py or environment variables
    api_key = getattr(settings, 'DHL_API_KEY', None)
    if not api_key:
        logger.error("DHL_API_KEY is not configured in settings.")
        return None # Return None on error

    url = f"https://api-eu.dhl.com/track/shipments"
    headers = {
        "DHL-API-Key": api_key
    }
    params = {
        "trackingNumber": tracking_number,
        "service": "express" # or the service you use
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()  # Raises an exception for bad status codes (4xx or 5xx)

        data = response.json()
        shipments = data.get('shipments', [])

        if not shipments:
            return []

        # Assuming we're interested in the first shipment returned
        events = shipments[0].get('events', [])

        parsed_events = []
        for event in events:
            # The DHL API timestamp format is like "2024-05-10T14:30:00Z"
            # We need to parse it into a timezone-aware datetime object.
            timestamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))

            parsed_events.append({
                'timestamp': timestamp,
                'description': event.get('description', 'No description'),
                'location': event.get('location', {}).get('address', {}).get('addressLocality', ''),
                'event_id': event.get('id', str(timestamp.timestamp())) # Use a unique ID from the event if available
            })

        return parsed_events

    except requests.RequestException as e:
        logger.error(f"Error fetching DHL tracking for {tracking_number}: {e}")
        return None # Return None on error
    except (KeyError, TypeError) as e:
        logger.error(f"Error parsing DHL response for {tracking_number}: {e}")
        return None # Return None on error


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
        expires_in = token_data.get('expires_in', 3540) # Default to 59 mins

        # Cache the token, subtracting a minute for safety
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
    Returns a list of events on success, None on failure.
    """
    access_token = get_fedex_access_token()
    if not access_token:
        # Error already logged in get_fedex_access_token
        return None

    url = "https://apis.fedex.com/track/v1/trackingnumbers"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "includeDetailedScans": True,
        "trackingInfo": [{
            "trackingNumberInfo": {
                "trackingNumber": tracking_number
            }
        }]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()

        data = response.json()
        track_results = data.get('output', {}).get('completeTrackResults', [])

        if not track_results or not track_results[0].get('trackResults'):
            logger.info(f"No tracking results found for FedEx number: {tracking_number}")
            return [] # No results is a valid success case, just no events.

        events = track_results[0]['trackResults'][0].get('scanEvents', [])

        parsed_events = []
        for event in events:
            timestamp_str = event.get('date')
            if timestamp_str:
                # Timestamps might not have timezone info, assume UTC
                dt_object = datetime.fromisoformat(timestamp_str)
                if dt_object.tzinfo is None:
                    dt_object = timezone.make_aware(dt_object, timezone.utc)
                timestamp = dt_object
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
        # Check if the response has content that can be logged
        if e.response is not None:
            logger.error(f"FedEx API Response Content: {e.response.text}")
        return None # Return None on error
    except (KeyError, TypeError) as e:
        logger.error(f"Error parsing Fedex response for {tracking_number}: {e}")
        return None # Return None on error

# --- Main Service Function (with improved logic) ---
def update_parcel_tracking_from_api(parcel: Parcel) -> (bool, str):
    """
    Fetches tracking data, updates logs, and intelligently updates the
    parcel's status based on a set of rules.
    """
    if not parcel.tracking_number:
        return False, "No tracking number."

    events = None # Initialize to None
    courier_name = parcel.courier_company.name.lower() if parcel.courier_company else ""

    if 'dhl' in courier_name:
        events = get_dhl_tracking_details(parcel.tracking_number)
    elif 'fedex' in courier_name:
        events = get_fedex_tracking_details(parcel.tracking_number)
    else:
        return False, f"No API handler for courier: {parcel.courier_company.name if parcel.courier_company else 'Unknown'}."

    # --- THIS IS THE KEY CHANGE FOR ERROR HANDLING ---
    # If events is None, it means an API or parsing error occurred.
    if events is None:
        return False, "API call failed. Could not retrieve tracking events."

    # If events is an empty list, it was successful but had no new info.
    if not events:
        return True, "No new tracking events found from API."

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

    # --- Status Update Logic (remains the same) ---
    is_returned = any('return' in event['description'].lower() for event in events)
    if is_returned and parcel.status != 'RETURNED':
        parcel.status = 'RETURNED'
        parcel.save(update_fields=['status'])
        return True, "Status updated to RETURNED."

    is_delivered = any('delivered' in event['description'].lower() for event in events)
    if is_delivered and parcel.status != 'DELIVERED':
        parcel.status = 'DELIVERED'
        parcel.save(update_fields=['status'])
        return True, "Status updated to DELIVERED."

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


def get_ups_access_token():
    """
    Retrieves a UPS API access token, utilizing cache.
    """
    token = cache.get('ups_access_token')
    if token:
        logger.debug("Found UPS access token in cache.")
        return token

    logger.info("No UPS access token in cache, requesting a new one.")
    client_id = getattr(settings, 'UPS_CLIENT_ID', None)
    client_secret = getattr(settings, 'UPS_CLIENT_SECRET', None)

    if not all([client_id, client_secret]):
        logger.error("UPS_CLIENT_ID or UPS_CLIENT_SECRET are not configured.")
        return None

    auth_url = "https://onlinetools.ups.com/security/v1/oauth/token" # Production URL

    # UPS requires Basic Auth for the token endpoint
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
    Fetches tracking details from the UPS API for a given tracking number.
    """
    access_token = get_ups_access_token()
    if not access_token:
        return None

    url = f"https://onlinetools.ups.com/api/track/v1/details/{tracking_number}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "transId": str(timezone.now().timestamp()), # Transaction ID for traceability
        "transactionSrc": "my-django-app"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        shipment = data.get('trackResponse', {}).get('shipment', [{}])[0]
        packages = shipment.get('package', [])
        if not packages:
            return []

        events = packages[0].get('activity', [])
        parsed_events = []
        for event in events:
            date_str = event.get('date')
            time_str = event.get('time')
            if date_str and time_str:
                # UPS Timestamp format: YYYYMMDD and HHMMSS
                timestamp = timezone.make_aware(
                    datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                )
            else:
                timestamp = timezone.now()

            location = event.get('location', {})
            city = location.get('address', {}).get('city', '')

            event_unique_id = f"{tracking_number}-{timestamp.isoformat()}-{event.get('status',{}).get('description')}"

            parsed_events.append({
                'timestamp': timestamp,
                'description': event.get('status', {}).get('description', 'No description'),
                'location': city,
                'event_id': event_unique_id
            })
        return parsed_events

    except requests.RequestException as e:
        logger.error(f"Error fetching UPS tracking for {tracking_number}: {e}")
        if e.response is not None:
            logger.error(f"UPS API Response Content: {e.response.text}")
        return None
    except (KeyError, TypeError, IndexError) as e:
        logger.error(f"Error parsing UPS response for {tracking_number}: {e}")
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
        # Check if parcel status needs to be updated even with no new events
        if parcel.status == 'IN_TRANSIT' and parcel.shipped_at and (timezone.now() - parcel.shipped_at > timezone.timedelta(days=20)):
            parcel.status = 'DELIVERY_FAILED'
            parcel.save(update_fields=['status'])
            return True, "No new events found, but status updated to DELIVERY FAILED (in transit > 20 days)."
        return True, "No new tracking events found from API."

    new_logs_created_count = 0
    # Sort events by timestamp before processing
    events.sort(key=lambda x: x['timestamp'])

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

    # Get the latest event description for status update logic
    latest_event_description = events[-1]['description'].lower() if events else ""

    if 'return' in latest_event_description and parcel.status != 'RETURNED':
        parcel.status = 'RETURNED'
        parcel.save(update_fields=['status'])
        return True, f"Status updated to RETURNED based on event: '{events[-1]['description']}'."

    if 'delivered' in latest_event_description and parcel.status != 'DELIVERED':
        parcel.status = 'DELIVERED'
        parcel.save(update_fields=['status'])
        return True, f"Status updated to DELIVERED based on event: '{events[-1]['description']}'."

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

