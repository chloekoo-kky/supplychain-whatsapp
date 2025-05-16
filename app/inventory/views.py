# app/inventory/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Sum, Q
from django.db import transaction
from django.contrib import messages # Import messages
from django.forms import inlineformset_factory
from django.utils.text import slugify


from .models import (
    InventoryBatchItem, Product, Supplier,
    StockTakeSession, StockTakeItem, StockDiscrepancy # New models
)
from warehouse.models import WarehouseProduct as NewAggregateWarehouseProduct, Warehouse # <<< ADDED Warehouse import
from .forms import (
    InventoryBatchItemForm, StockTakeItemForm, StockTakeItemFormSet, # New forms
    StockTakeSessionSelectionForm # New form
)

from django.contrib.admin.views.decorators import staff_member_required # For superuser/staff views
from django.http import HttpResponse
import csv
import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill



@login_required
def inventory_batch_list_view(request):
    user = request.user
    base_queryset = NewAggregateWarehouseProduct.objects.select_related(
        'product',
        'warehouse',
        'supplier'
    ).prefetch_related(
        'batches'
    )

    if not user.is_superuser and user.warehouse:
        base_queryset = base_queryset.filter(warehouse=user.warehouse)
    elif not user.is_superuser and not user.warehouse:
        base_queryset = base_queryset.none()
        messages.warning(request, "You are not assigned to a warehouse. Please contact an administrator.")

    warehouse_products_qs = base_queryset.order_by(
        'product__name',
        'warehouse__name'
    )

    today = timezone.now().date() # Local date, for display consistency
    processed_warehouse_products_list = []

    for wp in warehouse_products_qs:
        current_aggregate_stock = wp.quantity if wp.quantity is not None else 0

        sum_of_batched_stock = wp.batches.aggregate(total_qty=Sum('quantity'))['total_qty'] or 0
        wp.calculated_unbatched_quantity = current_aggregate_stock - sum_of_batched_stock

        processed_batches = []
        for batch in wp.batches.all():
            # The line below was causing the AttributeError and is removed.
            # batch.expiry_status_display = batch.expiry_status_display
            # The property batch.expiry_status_display will be accessed directly in the template.
            processed_batches.append(batch)

        wp.processed_batches = processed_batches # This list now contains original batch items
        processed_warehouse_products_list.append(wp)

    all_batches_qs = InventoryBatchItem.objects.select_related(
        'warehouse_product__product',
        'warehouse_product__warehouse'
    )
    if not user.is_superuser and user.warehouse:
        all_batches_qs = all_batches_qs.filter(warehouse_product__warehouse=user.warehouse)
    elif not user.is_superuser and not user.warehouse:
        all_batches_qs = all_batches_qs.none()

    all_inventory_batches_for_modals = all_batches_qs.order_by(
        'warehouse_product__product__name',
        'warehouse_product__warehouse__name',
        'batch_number',
        'location_label',
        'expiry_date'
    )

    add_batch_form_instance = InventoryBatchItemForm()
    # Ensure the queryset for the form's warehouse_product field is correctly filtered
    # This is now handled in the form's __init__ or by passing a filtered queryset to the form
    # For now, we'll filter it here before passing to the form context
    wp_form_queryset = NewAggregateWarehouseProduct.objects.select_related(
        'product', 'warehouse'
    ).order_by('product__name', 'warehouse__name')

    if not user.is_superuser and user.warehouse:
        wp_form_queryset = wp_form_queryset.filter(warehouse=user.warehouse)
    elif not user.is_superuser and not user.warehouse:
        wp_form_queryset = wp_form_queryset.none()

    add_batch_form_instance.fields['warehouse_product'].queryset = wp_form_queryset
    # If you set label_from_instance in the form's __init__, it's fine.
    # Otherwise, you could set it here if needed:
    # add_batch_form_instance.fields['warehouse_product'].label_from_instance = lambda obj: f"{obj.product.name} @ {obj.warehouse.name} (SKU: {obj.product.sku})"


    context = {
        'warehouse_products': processed_warehouse_products_list,
        'all_inventory_batches': all_inventory_batches_for_modals, # For edit modals
        'today_date_iso': today.strftime('%Y-%m-%d'),
        'add_batch_form': add_batch_form_instance,
        'page_title': 'Inventory'
    }
    return render(request, 'inventory/inventory_batch_list.html', context)


@login_required
@require_POST
def add_inventory_batch_view(request):
    user = request.user
    post_data = request.POST.copy()
    hidden_wp_id = post_data.get('hidden_warehouse_product_id')

    if hidden_wp_id:
        post_data['warehouse_product'] = hidden_wp_id

    selected_wp_id = post_data.get('warehouse_product')
    if selected_wp_id:
        try:
            wp_instance = NewAggregateWarehouseProduct.objects.get(pk=selected_wp_id)
            if not user.is_superuser and user.warehouse and wp_instance.warehouse != user.warehouse:
                return JsonResponse({'success': False, 'error': 'Permission denied. You cannot add a batch to this warehouse product.'}, status=403)
            elif not user.is_superuser and not user.warehouse:
                 return JsonResponse({'success': False, 'error': 'Permission denied. No warehouse assigned.'}, status=403)
        except NewAggregateWarehouseProduct.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Selected warehouse product does not exist.'}, status=400)
    else:
        return JsonResponse({'success': False, 'error': 'Warehouse product selection is missing.'}, status=400)

    # Pass the request to the form if it needs it (e.g., for user-based filtering within the form)
    form = InventoryBatchItemForm(post_data, request=request) # Pass request if form uses it

    if form.is_valid():
        try:
            batch_item = form.save(commit=False)
            # Ensure the warehouse_product on the batch_item itself is consistent with permissions
            if not user.is_superuser and user.warehouse and batch_item.warehouse_product.warehouse != user.warehouse:
                 return JsonResponse({'success': False, 'error': 'Permission denied. Mismatch in final warehouse assignment.'}, status=403)

            batch_item.save()
            # Optional: Trigger any necessary updates on the parent WarehouseProduct
            # if batch_item.warehouse_product and hasattr(batch_item.warehouse_product, 'update_stock_from_batches'):
            #    batch_item.warehouse_product.update_stock_from_batches()
            return JsonResponse({'success': True, 'message': 'Batch added successfully!'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': f'Error saving batch: {str(e)}'}, status=500)
    else:
        errors = {field: error[0] for field, error in form.errors.items()}
        return JsonResponse({'success': False, 'errors': errors, 'message': 'Please correct the form errors.'}, status=400)


@login_required
@require_POST
def edit_inventory_batch_view(request, batch_pk):
    user = request.user
    batch_item_instance = get_object_or_404(InventoryBatchItem.objects.select_related('warehouse_product__warehouse'), pk=batch_pk)

    if not user.is_superuser and user.warehouse:
        if batch_item_instance.warehouse_product.warehouse != user.warehouse:
            return JsonResponse({'success': False, 'error': 'Permission denied. You cannot edit this batch item.'}, status=403)
    elif not user.is_superuser and not user.warehouse:
        return JsonResponse({'success': False, 'error': 'Permission denied. No warehouse assigned.'}, status=403)

    post_data = request.POST.copy()
    if 'warehouse_product' in post_data: # If user tries to change the warehouse_product link
        try:
            wp_instance = NewAggregateWarehouseProduct.objects.get(pk=post_data['warehouse_product'])
            if not user.is_superuser and user.warehouse and wp_instance.warehouse != user.warehouse:
                return JsonResponse({'success': False, 'error': 'Permission denied. Cannot reassign batch to this warehouse product.'}, status=403)
        except NewAggregateWarehouseProduct.DoesNotExist:
             return JsonResponse({'success': False, 'error': 'Target warehouse product for reassignment does not exist.'}, status=400)

    form = InventoryBatchItemForm(post_data, instance=batch_item_instance, request=request) # Pass request if form uses it
    if form.is_valid():
        try:
            updated_batch_item = form.save()

            # Optional: Trigger updates on parent(s) if warehouse_product changed
            # if form.changed_data and 'warehouse_product' in form.changed_data:
            #    if hasattr(updated_batch_item.warehouse_product, 'update_stock_from_batches'):
            #        updated_batch_item.warehouse_product.update_stock_from_batches()
            #    old_wp_id = form.initial.get('warehouse_product')
            #    if old_wp_id:
            #        try:
            #            old_wp = NewAggregateWarehouseProduct.objects.get(pk=old_wp_id)
            #            if hasattr(old_wp, 'update_stock_from_batches'):
            #                old_wp.update_stock_from_batches()
            #        except NewAggregateWarehouseProduct.DoesNotExist:
            #            pass # Old WP might have been deleted or is invalid

            expiry_date_formatted = updated_batch_item.expiry_date.strftime('%d/%m/%Y') if updated_batch_item.expiry_date else 'N/A'
            date_received_formatted = updated_batch_item.date_received.strftime('%d/%m/%Y') if updated_batch_item.date_received else '-'
            location_label_display = updated_batch_item.location_label if updated_batch_item.location_label else '-'

            return JsonResponse({
                'success': True,
                'message': 'Batch updated successfully!',
                'batch_item': {
                    'pk': updated_batch_item.pk,
                    'batch_number': updated_batch_item.batch_number or "N/A",
                    'location_label': location_label_display,
                    'expiry_date_formatted': expiry_date_formatted,
                    'quantity': updated_batch_item.quantity,
                    'cost_price': str(updated_batch_item.cost_price) if updated_batch_item.cost_price is not None else '-',
                    'date_received_formatted': date_received_formatted,
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': f'Error updating batch: {str(e)}'}, status=500)
    else:
        errors = {field: error[0] for field, error in form.errors.items()}
        return JsonResponse({'success': False, 'errors': errors, 'message': 'Please correct the form errors.'}, status=400)

@login_required
def supplier_list(request):
    user = request.user
    if not user.is_superuser and user.warehouse:
        suppliers_qs = Supplier.objects.filter(
            products__warehouseproduct__warehouse=user.warehouse
        ).distinct().order_by('name')
    else:
        suppliers_qs = Supplier.objects.all().order_by('name')

    return render(request, 'inventory/supplier_list.html', {'suppliers': suppliers_qs})

@login_required
def stock_take_operator_view(request):
    user = request.user
    if not user.warehouse:
        messages.error(request, "You are not assigned to a warehouse. Stock take cannot be performed.")
        return redirect('inventory:inventory_batch_list_view')

    active_stock_take_session = None
    session_id_from_url = request.GET.get('session_id')

    if session_id_from_url:
        try:
            active_stock_take_session = StockTakeSession.objects.get(
                pk=session_id_from_url,
                warehouse=user.warehouse,
                status__in=['PENDING', 'COMPLETED_BY_OPERATOR']
            )
        except StockTakeSession.DoesNotExist:
            messages.error(request, "Invalid or inaccessible stock take session selected.")
            return redirect('inventory:stock_take_operator')

    if request.method == 'POST':
        if 'select_or_create_session' in request.POST:
            selection_form = StockTakeSessionSelectionForm(request.POST, warehouse=user.warehouse)
            if selection_form.is_valid():
                selected_session = selection_form.cleaned_data.get('active_session')
                new_session_name_input = selection_form.cleaned_data.get('session_name')

                if selected_session:
                    active_stock_take_session = selected_session
                    messages.success(request, f"Selected stock take session: {active_stock_take_session.name}")
                elif new_session_name_input: # If a name was provided (even if auto-generated and then submitted)
                    active_stock_take_session = StockTakeSession.objects.create(
                        name=new_session_name_input, # Use the submitted name
                        warehouse=user.warehouse,
                        initiated_by=user,
                        status='PENDING'
                    )
                    messages.success(request, f"New stock take session started: {active_stock_take_session.name}")
                else:
                    messages.error(request, "Invalid session selection or creation attempt.")
                    return redirect('inventory:stock_take_operator')
                return redirect(f"{request.path}?session_id={active_stock_take_session.pk}")

        elif 'submit_stock_take_items' in request.POST or 'mark_session_complete' in request.POST:
            session_pk_from_form = request.POST.get('session_pk')
            if not session_pk_from_form:
                messages.error(request, "Session identifier missing from submission.")
                return redirect('inventory:stock_take_operator')
            try:
                current_session = StockTakeSession.objects.get(pk=session_pk_from_form, warehouse=user.warehouse)
                if current_session.status not in ['PENDING', 'COMPLETED_BY_OPERATOR']:
                     messages.error(request, "This stock take session can no longer be modified.")
                     return redirect(f"{request.path}?session_id={current_session.pk}")
                active_stock_take_session = current_session
            except StockTakeSession.DoesNotExist:
                messages.error(request, "Stock take session not found or not accessible.")
                return redirect('inventory:stock_take_operator')

            if current_session.status == 'COMPLETED_BY_OPERATOR':
                # If the session is ALREADY marked as complete by operator
                if 'mark_session_complete' in request.POST:
                    messages.warning(request, f"Session '{current_session.name}' is already marked as complete. No further action taken.")
                else: # Trying to save items to an already completed session
                    messages.error(request, f"Session '{current_session.name}' is already complete. No further items can be saved.")
                return redirect(f"{request.path}?session_id={current_session.pk}")

            elif current_session.status != 'PENDING':
                # For any other status that isn't PENDING or COMPLETED_BY_OPERATOR (e.g., EVALUATED, CLOSED)
                messages.error(request, f"Session '{current_session.name}' status ({current_session.get_status_display()}) does not allow operator modifications.")
                return redirect(f"{request.path}?session_id={current_session.pk}")

            formset = StockTakeItemFormSet(
                request.POST,
                instance=current_session,
                prefix='stocktakeitems',
                form_kwargs={'user': user, 'warehouse': user.warehouse}
            )
            if formset.is_valid():
                formset.save()
                messages.success(request, f"Stock take items saved for session: {current_session.name}")

                if 'mark_session_complete' in request.POST:
                    current_session.status = 'COMPLETED_BY_OPERATOR'
                    current_session.completed_by_operator_at = timezone.now()
                    current_session.save()
                    messages.success(request, f"Stock take session '{current_session.name}' marked as complete by operator.")

                    return redirect('inventory:inventory_batch_list_view')
                return redirect(f"{request.path}?session_id={current_session.pk}")
            else:
                messages.error(request, "Please correct the errors in the stock take items.")
        else:
            messages.error(request, "Invalid action or stock take session not active.")
            return redirect('inventory:stock_take_operator')

    item_formset = None
    selection_form = None

    # Data for auto-generating session name
    current_date_yyyymmdd = timezone.now().strftime('%Y%m%d')
    warehouse_name_slug = slugify(user.warehouse.name) if user.warehouse else "unknown_wh"
    # Prefer user.name if available, otherwise fallback to email prefix
    username_slug = slugify(user.name.split('@')[0] if user.name else user.email.split('@')[0]) if user.is_authenticated else "unknown_user"
    auto_generated_session_name_prefix = f"{current_date_yyyymmdd}_{warehouse_name_slug}_{username_slug}"


    if active_stock_take_session:
        item_formset = StockTakeItemFormSet(
            instance=active_stock_take_session,
            prefix='stocktakeitems',
            form_kwargs={'user': user, 'warehouse': user.warehouse}
        )
    else:
        selection_form = StockTakeSessionSelectionForm(warehouse=user.warehouse)
        # We can pre-fill the session_name field if it's a new session context
        # However, the JS will handle this dynamically.
        # If you wanted to pre-fill via Django form initial:
        # selection_form = StockTakeSessionSelectionForm(
        #     warehouse=user.warehouse,
        #     initial={'session_name': auto_generated_session_name_prefix + "_COUNT1"} # Example
        # )

    is_readonly_for_template_flag = False
    if active_stock_take_session and active_stock_take_session.status != 'PENDING':
        is_readonly_for_template_flag = True

    context = {
        'selection_form': selection_form,
        'item_formset': item_formset,
        'active_session': active_stock_take_session,
        'warehouse': user.warehouse,
        'page_title': f"Stock Take for {user.warehouse.name}" if user.warehouse else "Stock Take",
        # Pass data for JS auto-generation
        'current_date_yyyymmdd': current_date_yyyymmdd,
        'warehouse_name_slug': warehouse_name_slug,
        'username_slug': username_slug,
        'auto_generated_session_name_prefix': auto_generated_session_name_prefix, # Pass the full prefix
        'is_session_readonly_for_template': is_readonly_for_template_flag,
    }
    return render(request, 'inventory/stock_take_form.html', context)

@staff_member_required # Ensures only staff (including superusers) can access
def stock_take_session_list_view(request):
    """
    View for superusers to list all StockTakeSession objects.
    Allows filtering by warehouse and status.
    """
    if not request.user.is_superuser: # Extra check if only superusers, not just staff
        messages.error(request, "You do not have permission to view this page.")
        return redirect('inventory:inventory_batch_list_view')

    sessions_qs = StockTakeSession.objects.select_related('warehouse', 'initiated_by', 'evaluated_by').all()

    # Filtering
    warehouse_filter = request.GET.get('warehouse')
    status_filter = request.GET.get('status')

    if warehouse_filter:
        sessions_qs = sessions_qs.filter(warehouse_id=warehouse_filter)
    if status_filter:
        sessions_qs = sessions_qs.filter(status=status_filter)

    warehouses = Warehouse.objects.all().order_by('name')
    status_choices = StockTakeSession.STATUS_CHOICES

    context = {
        'sessions': sessions_qs,
        'warehouses': warehouses,
        'status_choices': status_choices,
        'selected_warehouse': warehouse_filter,
        'selected_status': status_filter,
        'page_title': "Stock Take Sessions"
    }
    return render(request, 'inventory/stock_take_session_list.html', context)


@staff_member_required
def stock_take_session_detail_view(request, session_pk):
    """
    View for superusers to see the details and items of a specific StockTakeSession.
    """
    if not request.user.is_superuser:
        messages.error(request, "You do not have permission to view this page.")
        return redirect('inventory:stock_take_session_list')

    session = get_object_or_404(
        StockTakeSession.objects.select_related('warehouse', 'initiated_by', 'evaluated_by')
                           .prefetch_related(
                               'items',
                               'items__warehouse_product__product', # For display
                               'items__warehouse_product__warehouse' # For display consistency
                           ),
        pk=session_pk
    )

    # If you want to allow superusers to edit items from this view, you'd use the formset.
    # For now, this view is read-only for items.
    # item_formset = StockTakeItemFormSet(instance=session, prefix='stocktakeitems_detail', form_kwargs={'user': request.user, 'warehouse': session.warehouse})


    context = {
        'session': session,
        # 'item_formset': item_formset, # Uncomment if making items editable here
        'page_title': f"Details for Stock Take: {session.name}"
    }
    return render(request, 'inventory/stock_take_session_detail.html', context)


@staff_member_required
def download_stock_take_session_csv(request, session_pk):
    """
    Allows superusers to download the items of a stock take session as a CSV file.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("You do not have permission to perform this action.")

    session = get_object_or_404(StockTakeSession, pk=session_pk)
    items = session.items.select_related(
        'warehouse_product__product',
        'warehouse_product__warehouse'
    ).order_by('warehouse_product__product__sku', 'location_label_counted', 'batch_number_counted')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="stock_take_session_{session.pk}_{session.name.replace(" ", "_")}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Session ID', 'Session Name', 'Session Warehouse', 'Session Status',
        'Item ID (System)', 'Product SKU (System)', 'Product Name (System)', 'Warehouse (System)',
        'Location (Counted)', 'Batch No. (Counted)', 'Expiry (Counted)', 'Quantity (Counted)',
        'Item Notes', 'Counted At'
    ])

    for item in items:
        writer.writerow([
            session.pk,
            session.name,
            session.warehouse.name,
            session.get_status_display(),
            item.warehouse_product.pk,
            item.warehouse_product.product.sku,
            item.warehouse_product.product.name,
            item.warehouse_product.warehouse.name, # Should match session.warehouse.name
            item.location_label_counted if item.location_label_counted else '',
            item.batch_number_counted if item.batch_number_counted else '',
            item.expiry_date_counted.strftime('%Y-%m-%d') if item.expiry_date_counted else '',
            item.counted_quantity,
            item.notes,
            item.counted_at.strftime('%Y-%m-%d %H:%M:%S') if item.counted_at else ''
        ])
    return response

@staff_member_required
@require_POST # Evaluation should be a POST request as it changes data
@transaction.atomic # Ensure all discrepancy creations are atomic
def evaluate_stock_take_session_view(request, session_pk):
    if not request.user.is_superuser:
        messages.error(request, "You do not have permission to perform this action.")
        return redirect('inventory:stock_take_session_list')

    session = get_object_or_404(StockTakeSession, pk=session_pk)

    if session.status not in ['COMPLETED_BY_OPERATOR', 'EVALUATED']: # Allow re-evaluation
        messages.error(request, f"Session '{session.name}' is not ready for evaluation or has already been closed.")
        return redirect('inventory:stock_take_session_detail', session_pk=session.pk)

    # Clear existing discrepancies for this session to make evaluation idempotent
    StockDiscrepancy.objects.filter(session=session).delete()

    counted_items = StockTakeItem.objects.filter(session=session).select_related('warehouse_product')
    system_items = InventoryBatchItem.objects.filter(
        warehouse_product__warehouse=session.warehouse
    ).select_related('warehouse_product', 'warehouse_product__product')

    # --- Normalize and Aggregate Counted Items ---
    # Key: (warehouse_product_id, location_label_counted, batch_number_counted, expiry_date_counted)
    aggregated_counted = {}
    for item in counted_items:
        key = (
            item.warehouse_product_id,
            item.location_label_counted if item.location_label_counted else "", # Normalize None/empty
            item.batch_number_counted if item.batch_number_counted else "",   # Normalize None/empty
            item.expiry_date_counted
        )
        if key not in aggregated_counted:
            aggregated_counted[key] = {
                'quantity': 0,
                'warehouse_product': item.warehouse_product,
                'location': item.location_label_counted,
                'batch': item.batch_number_counted,
                'expiry': item.expiry_date_counted,
                'notes': [], # Collect notes from multiple entries if any
                'original_sti_pks': [] # Keep track of original StockTakeItem pks
            }
        aggregated_counted[key]['quantity'] += item.counted_quantity
        if item.notes:
            aggregated_counted[key]['notes'].append(item.notes)
        aggregated_counted[key]['original_sti_pks'].append(item.pk)


    # --- Normalize and Aggregate System Items (InventoryBatchItem) ---
    # Key: (warehouse_product_id, location_label, batch_number, expiry_date)
    aggregated_system = {}
    for item in system_items:
        key = (
            item.warehouse_product_id,
            item.location_label if item.location_label else "",
            item.batch_number if item.batch_number else "",
            item.expiry_date
        )
        if key not in aggregated_system: # Should be unique by definition of InventoryBatchItem unique_together
            aggregated_system[key] = {
                'quantity': item.quantity,
                'warehouse_product': item.warehouse_product,
                'location': item.location_label,
                'batch': item.batch_number,
                'expiry': item.expiry_date,
                'original_ibi_pk': item.pk
            }
        else:
            # This case should ideally not happen if your InventoryBatchItem has unique constraints
            # on (wp, location, batch, expiry). If it can, sum quantities.
            aggregated_system[key]['quantity'] += item.quantity


    discrepancies_created = 0
    processed_system_keys = set()

    # 1. Iterate through aggregated counted items
    for key, counted_data in aggregated_counted.items():
        system_data = aggregated_system.get(key)
        discrepancy_type = ''
        discrepancy_qty = 0
        system_qty_for_calc = 0
        system_ibi_pk = None

        if system_data: # Match found based on key
            processed_system_keys.add(key) # Mark this system key as processed
            system_qty_for_calc = system_data['quantity']
            system_ibi_pk = system_data.get('original_ibi_pk')

            discrepancy_qty = counted_data['quantity'] - system_data['quantity']
            if discrepancy_qty == 0:
                discrepancy_type = 'MATCH'
            elif discrepancy_qty > 0:
                discrepancy_type = 'OVER'
            else: # discrepancy_qty < 0
                discrepancy_type = 'SHORT'
        else: # No exact match in system for this counted item configuration
            discrepancy_type = 'NOT_IN_SYSTEM'
            discrepancy_qty = counted_data['quantity'] # The entire counted amount is "extra"
            system_qty_for_calc = 0 # System has 0 of this specific configuration

        # Create StockDiscrepancy record
        # For NOT_IN_SYSTEM, system_inventory_batch_item will be None
        # For MATCH/OVER/SHORT, link to the specific InventoryBatchItem if possible
        StockDiscrepancy.objects.create(
            session=session,
            warehouse_product=counted_data['warehouse_product'],
            system_inventory_batch_item_id=system_ibi_pk if system_data else None,
            system_location_label=system_data['location'] if system_data else None,
            system_batch_number=system_data['batch'] if system_data else None,
            system_expiry_date=system_data['expiry'] if system_data else None,
            system_quantity=system_qty_for_calc,
            # For stock_take_item_reference, if multiple STIs aggregated, maybe leave null or link first one
            # For simplicity, let's leave it null if aggregated from multiple, or link if single.
            stock_take_item_reference_id=counted_data['original_sti_pks'][0] if len(counted_data['original_sti_pks']) == 1 else None,
            counted_location_label=counted_data['location'],
            counted_batch_number=counted_data['batch'],
            counted_expiry_date=counted_data['expiry'],
            counted_quantity=counted_data['quantity'],
            discrepancy_quantity=discrepancy_qty,
            discrepancy_type=discrepancy_type,
            notes="Counted notes: " + "; ".join(counted_data['notes']) if counted_data['notes'] else ""
        )
        discrepancies_created += 1

    # 2. Iterate through system items not processed yet (i.e., not found in counted items)
    for key, system_data in aggregated_system.items():
        if key not in processed_system_keys:
            StockDiscrepancy.objects.create(
                session=session,
                warehouse_product=system_data['warehouse_product'],
                system_inventory_batch_item_id=system_data.get('original_ibi_pk'),
                system_location_label=system_data['location'],
                system_batch_number=system_data['batch'],
                system_expiry_date=system_data['expiry'],
                system_quantity=system_data['quantity'],
                counted_quantity=0, # Not counted
                discrepancy_quantity=-system_data['quantity'], # Negative, as it's a shortage vs count
                discrepancy_type='NOT_COUNTED',
                notes="Item found in system but not in stock take count."
            )
            discrepancies_created += 1

    # Update session status
    session.status = 'EVALUATED'
    session.evaluated_by = request.user
    session.evaluated_at = timezone.now()
    session.save()

    messages.success(request, f"Stock take session '{session.name}' evaluated. {discrepancies_created} discrepancy/match records created.")
    return redirect('inventory:stock_take_session_detail', session_pk=session.pk)


@login_required
def search_warehouse_products_for_stocktake_json(request):
    """
    AJAX endpoint to search for WarehouseProduct items by code, SKU, or name
    within a specific warehouse. Used for stock take product selection.
    """
    term = request.GET.get('term', '').strip()
    warehouse_id = request.GET.get('warehouse_id') # Expecting warehouse_id of the current session

    if not warehouse_id:
        return JsonResponse({'error': 'Warehouse ID is required.'}, status=400)

    try:
        warehouse = Warehouse.objects.get(pk=warehouse_id)
    except Warehouse.DoesNotExist:
        return JsonResponse({'error': 'Invalid Warehouse ID.'}, status=400)

    # Ensure the requesting user has access to this warehouse if not a superuser
    if not request.user.is_superuser and request.user.warehouse != warehouse:
        return JsonResponse({'error': 'Access to this warehouse is denied.'}, status=403)

    results = []
    if term:
        # Search by WarehouseProduct.code, Product.sku, or Product.name
        # Prioritize code matches
        query = Q(code__icontains=term, warehouse=warehouse) | \
                Q(product__sku__icontains=term, warehouse=warehouse) | \
                Q(product__name__icontains=term, warehouse=warehouse)

        warehouse_products = NewAggregateWarehouseProduct.objects.filter(query).select_related('product', 'warehouse')[:10] # Limit results

        for wp in warehouse_products:
            label = f"{wp.code if wp.code else 'N/A'} - {wp.product.sku} - {wp.product.name} (@{wp.warehouse.name})"
            results.append({
                'id': wp.id,                     # WarehouseProduct ID
                'value': wp.code if wp.code else wp.product.sku, # Value for the input after selection (can be code or SKU)
                'label': label,                  # Text displayed in suggestions
                'name': wp.product.name,
                'sku': wp.product.sku,
                'wp_code': wp.code
            })

    return JsonResponse(results, safe=False)

@staff_member_required
def download_stock_take_evaluation_excel(request, session_pk):
    """
    Generates and downloads an Excel report for a stock take session's evaluation,
    focusing on discrepancies.
    """
    if not request.user.is_superuser:
        messages.error(request, "You do not have permission to perform this action.")
        return redirect('inventory:stock_take_session_list') # Or appropriate redirect

    session = get_object_or_404(
        StockTakeSession.objects.select_related(
            'warehouse', 'initiated_by', 'evaluated_by'
        ).prefetch_related(
            'discrepancies__warehouse_product__product',
            'discrepancies__warehouse_product__warehouse',
            'discrepancies__system_inventory_batch_item', # For more system details if needed
            'discrepancies__stock_take_item_reference' # For more counted details if needed
        ),
        pk=session_pk
    )

    if session.status != 'EVALUATED' and not session.discrepancies.exists():
        messages.warning(request, f"Session '{session.name}' has not been evaluated yet or has no discrepancies to report.")
        return redirect('inventory:stock_take_session_detail', session_pk=session.pk)

    # Create an Excel workbook and select the active sheet
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = f"Evaluation Report - {session.id}"

    # Define Headers
    headers = [
        "Product SKU", "Product Name", "Warehouse",
        "Discrepancy Type",
        "System Batch", "System Location", "System Expiry", "System Qty",
        "Counted Batch", "Counted Location", "Counted Expiry", "Counted Qty",
        "Discrepancy Qty",
        "Notes", "Resolved", "Resolution Notes", "Resolved By", "Resolved At"
    ]
    sheet.append(headers)

    # Apply styles to header row
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    for col_num, header_title in enumerate(headers, 1):
        cell = sheet.cell(row=1, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        sheet.column_dimensions[get_column_letter(col_num)].width = 20 # Adjust width as needed

    # Populate data rows
    row_num = 2
    for discrepancy in session.discrepancies.all():
        resolved_by_name = ""
        if discrepancy.resolved_by:
            resolved_by_name = discrepancy.resolved_by.name or discrepancy.resolved_by.email

        data_row = [
            discrepancy.warehouse_product.product.sku,
            discrepancy.warehouse_product.product.name,
            discrepancy.warehouse_product.warehouse.name,
            discrepancy.get_discrepancy_type_display(),
            discrepancy.system_batch_number or "-",
            discrepancy.system_location_label or "-",
            discrepancy.system_expiry_date.strftime('%Y-%m-%d') if discrepancy.system_expiry_date else "-",
            discrepancy.system_quantity if discrepancy.system_quantity is not None else "N/A",
            discrepancy.counted_batch_number or "-",
            discrepancy.counted_location_label or "-",
            discrepancy.counted_expiry_date.strftime('%Y-%m-%d') if discrepancy.counted_expiry_date else "-",
            discrepancy.counted_quantity if discrepancy.counted_quantity is not None else "N/A",
            discrepancy.discrepancy_quantity,
            discrepancy.notes or "-",
            "Yes" if discrepancy.is_resolved else "No",
            discrepancy.resolution_notes or "-",
            resolved_by_name or "-",
            discrepancy.resolved_at.strftime('%Y-%m-%d %H:%M') if discrepancy.resolved_at else "-",
        ]
        sheet.append(data_row)

        # Apply alternating row colors for readability (optional)
        fill_color = "DDEBF7" if row_num % 2 == 0 else "FFFFFF"
        row_fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
        for col_num in range(1, len(headers) + 1):
            sheet.cell(row=row_num, column=col_num).fill = row_fill
        row_num += 1

    # Auto-size columns (can be slow for very large datasets)
    # for column_cells in sheet.columns:
    #     length = max(len(str(cell.value) or "") for cell in column_cells)
    #     sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = length + 2

    # Prepare the HTTP response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    safe_session_name = "".join(c if c.isalnum() else "_" for c in session.name)
    response['Content-Disposition'] = f'attachment; filename="stock_take_evaluation_{session.pk}_{safe_session_name}.xlsx"'
    workbook.save(response)

    return response
