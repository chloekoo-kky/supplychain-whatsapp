def get_next_status(current_status):
    ordered_statuses = ['DRAFT', 'WAITING_INVOICE', 'PAYMENT_MADE', 'PARTIALLY_DELIVERED', 'DELIVERED', 'CANCELLED']
    try:
        idx = ordered_statuses.index(current_status)
        return ordered_statuses[idx + 1] if idx + 1 < len(ordered_statuses) else None
    except ValueError:
        return None
