# app/customers/utils.py

from .models import Customer
from django.db.models import Q
import re

def normalize_phone(phone_number):
    """Strips all non-digit characters from a phone number."""
    if not phone_number:
        return None
    return re.sub(r'\D', '', str(phone_number))

def get_or_create_customer_from_import(
    customer_name,
    company_name=None,
    phone_number=None,
    address_info=None,
    vat_number=None
):
    """
    Finds an existing customer based on the provided details or creates a new one.
    This function uses a cascade of checks to find the best match.

    Args:
        customer_name (str): The name of the customer.
        company_name (str, optional): The company name.
        phone_number (str, optional): The customer's phone number.
        address_info (dict, optional): A dict with address fields.
        vat_number (str, optional): The customer's VAT number.

    Returns:
        tuple: (Customer object, created_boolean)
    """
    if address_info is None:
        address_info = {}

    # --- Attempt 1: Match by unique, normalized phone number (most reliable) ---
    normalized_phone = normalize_phone(phone_number)
    if normalized_phone:
        try:
            customer = Customer.objects.get(phone_number=normalized_phone)
            # You might want to update the customer record here with new info if it differs
            # For now, we just return the found customer.
            return customer, False # (customer, created=False)
        except Customer.DoesNotExist:
            pass # Not found, proceed to next check

    # --- Attempt 2: Match by Customer Name and a key address part (e.g., ZIP code) ---
    # This helps differentiate customers with the same name.
    if customer_name and address_info.get('zip_code'):
        try:
            customer = Customer.objects.get(
                customer_name__iexact=customer_name,
                zip_code__iexact=address_info.get('zip_code')
            )
            return customer, False
        except Customer.DoesNotExist:
            pass
        except Customer.MultipleObjectsReturned:
            # Handle cases where this isn't unique enough
            pass

    # --- Attempt 3: If no match found, create a new customer ---
    new_customer = Customer.objects.create(
        customer_name=customer_name,
        company_name=company_name,
        phone_number=normalized_phone,
        address_line1=address_info.get('address_line1', ''),
        city=address_info.get('city', ''),
        state=address_info.get('state', ''),
        zip_code=address_info.get('zip_code', ''),
        country=address_info.get('country', ''),
        vat_number=vat_number
    )
    return new_customer, True # (customer, created=True)
