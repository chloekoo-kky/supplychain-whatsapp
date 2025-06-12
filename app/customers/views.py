from django.shortcuts import render

# Create your views here.
# app/customers/views.py

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from .models import Customer
from operation.models import Order, Parcel, OrderItem

@login_required
def customer_list_view(request):
    """
    Displays a searchable list of all customers and their complete order history.
    """
    query = request.GET.get('q', '').strip()

    # Start with the base queryset for customers
    customers_qs = Customer.objects.all()

    if query:
        # Filter based on search query
        customers_qs = customers_qs.filter(
            Q(customer_id__icontains=query) |
            Q(customer_name__icontains=query) |
            Q(company_name__icontains=query) |
            Q(phone_number__icontains=query) |
            Q(email__icontains=query)
        )

    # Use prefetch_related to efficiently load all related data in minimal queries
    customers_with_details = customers_qs.prefetch_related(
        Prefetch('orders', queryset=Order.objects.select_related('warehouse').order_by('-order_date')),
        Prefetch('orders__items', queryset=OrderItem.objects.select_related('product')),
        Prefetch('orders__parcels', queryset=Parcel.objects.select_related('courier_company'))
    ).order_by('customer_name')

    context = {
        'page_title': "Customer Directory",
        'customers': customers_with_details,
        'query': query,
    }
    return render(request, 'customers/customer_list.html', context)
