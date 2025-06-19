# # app/operation/tasks.py

# from celery import shared_task
# from .models import Parcel, ParcelTrackingLog
# from .services import get_dhl_tracking_details

# @shared_task
# def update_parcel_tracking_status(parcel_id):
#     """
#     A Celery task to fetch tracking updates and intelligently update the
#     parcel's internal status.
#     """
#     try:
#         parcel = Parcel.objects.get(id=parcel_id)
#     except Parcel.DoesNotExist:
#         return f"Parcel with ID {parcel_id} not found."

#     if not parcel.tracking_number:
#         return f"Parcel {parcel_id} has no tracking number."

#     if parcel.courier_company and 'dhl' in parcel.courier_company.name.lower():
#         events = get_dhl_tracking_details(parcel.tracking_number)

#         new_events_count = 0
#         for event_data in events:
#             log_entry, created = ParcelTrackingLog.objects.update_or_create(
#                 parcel=parcel,
#                 event_id=event_data.get('event_id'),
#                 defaults={
#                     'timestamp': event_data['timestamp'],
#                     'status_description': event_data['description'],
#                     'location': event_data['location'],
#                 }
#             )
#             if created:
#                 new_events_count += 1

#         # --- START: Corrected Status Update Logic ---
#         # Only update the parcel's main status if there are new events from the courier.
#         if new_events_count > 0:
#             latest_log = parcel.tracking_logs.first()
#             if latest_log:
#                 new_status = parcel.status  # Start with the current status
#                 latest_description = latest_log.status_description.lower()

#                 # Interpret the description from DHL and map it to our internal status choices
#                 if 'delivered' in latest_description:
#                     new_status = 'DELIVERED'
#                 elif 'failed' in latest_description or 'exception' in latest_description:
#                     new_status = 'DELIVERY_FAILED'
#                 # If the parcel isn't already in a final state, any new update means it's in transit.
#                 elif parcel.status not in ['DELIVERED', 'DELIVERY_FAILED', 'RETURNED_COURIER']:
#                     new_status = 'IN_TRANSIT'

#                 # Only save to the database if the status has actually changed.
#                 if parcel.status != new_status:
#                     parcel.status = new_status
#                     parcel.save(update_fields=['status'])
#         # --- END: Corrected Status Update Logic ---

#         return f"Found {len(events)} events for parcel {parcel_id}. Created {new_events_count} new logs."
#     else:
#         return f"Courier for parcel {parcel_id} is not DHL. Skipping."
