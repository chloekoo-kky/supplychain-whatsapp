# app/customers/models.py

from django.db import models
import uuid

class Customer(models.Model):
    """
    Stores a single customer record, which can be linked to multiple orders.
    """
    # A unique internal ID for easier reference
    customer_id = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        help_text="Unique internal customer ID"
    )
    customer_name = models.CharField(max_length=255, db_index=True)
    company_name = models.CharField(max_length=255, blank=True, null=True, db_index=True)

    # Contact Info
    phone_number = models.CharField(max_length=30, blank=True, null=True, unique=True, db_index=True, help_text="Unique phone number to identify customers.")
    email = models.EmailField(max_length=255, blank=True, null=True, unique=True, db_index=True)

    # Address Info
    address_line1 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)

    vat_number = models.CharField(max_length=50, blank=True, null=True)
    notes = models.TextField(blank=True, null=True, help_text="Internal notes about the customer.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['customer_name', 'company_name']
        verbose_name = "Customer"
        verbose_name_plural = "Customers"

    def __str__(self):
        if self.company_name:
            return f"{self.company_name} ({self.customer_name})"
        return self.customer_name

    def save(self, *args, **kwargs):
        # Generate a unique customer_id if one doesn't exist
        if not self.customer_id:
            # A simple way to generate an ID: CUST + 6 random hex chars
            self.customer_id = f"CUST-{''.join(uuid.uuid4().hex.upper().split('-'))[:6]}"
        super().save(*args, **kwargs)
