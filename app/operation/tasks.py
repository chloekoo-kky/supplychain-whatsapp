from celery import shared_task
from .models import Parcel, CourierInvoiceItem

@shared_task
def match_and_update_parcels():
    """
    Task to find parcels that have been billed and update their status.
    """
    # Find billed items that haven't been linked to a parcel yet
    billed_items_to_process = CourierInvoiceItem.objects.filter(parcel__isnull=True)

    for item in billed_items_to_process:
        try:
            # Find the matching parcel by tracking number
            parcel_to_update = Parcel.objects.get(tracking_number=item.tracking_number)

            # Update the parcel
            parcel_to_update.actual_shipping_cost = item.actual_cost
            parcel_to_update.status = 'BILLED'
            parcel_to_update.save()

            # Link the invoice item to the parcel
            item.parcel = parcel_to_update
            item.save()

        except Parcel.DoesNotExist:
            # Handle cases where the tracking number from the invoice doesn't match any parcel
            print(f"No matching parcel found for tracking number: {item.tracking_number}")
        except Parcel.MultipleObjectsReturned:
            # Handle cases where the tracking number is duplicated (should be rare)
             print(f"Multiple parcels found for tracking number: {item.tracking_number}")
