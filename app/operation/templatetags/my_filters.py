import builtins
from django import template
from django.utils.safestring import mark_safe


register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def subtract(value, arg):
    try:
        return value - arg
    except (TypeError, ValueError):
        return ''

@register.filter(name='underline_last_n')
def underline_last_n(value, n):
    """
    Wraps the last n characters of a string in a span for styling.
    """
    if not isinstance(value, str) or not isinstance(n, int) or n <= 0:
        return value

    if len(value) <= n:
        return mark_safe(f'<span class="underlined-suffix">{value}</span>')

    start = value[:-n]
    end = value[-n:]

    return mark_safe(f'{start}<span class="underlined-suffix">{end}</span>')

@register.filter(name='title_case')
def title_case(value):
    """Converts a string into title case."""
    if isinstance(value, str):
        return value.title()
    return value


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """
    Replaces or adds query parameters to the current URL.
    This is used to build URLs for sorting and pagination links that preserve existing filters.
    """
    query = context['request'].GET.copy()
    for key, value in kwargs.items():
        query[key] = str(value) # Ensure value is a string
    return query.urlencode()

@register.simple_tag
def next_sort(field_name, current_sort_field):
    """
    Determines the next sorting direction for a column header link.
    - If not sorted by this field, set to ascending.
    - If sorted ascending, set to descending.
    - If sorted descending, set back to ascending.
    """
    if field_name == current_sort_field.strip('-'):
        if current_sort_field.startswith('-'):
            return field_name  # From descending to ascending
        else:
            return f'-{field_name}'  # From ascending to descending
    return field_name  # Default to ascending for a new field

@register.filter(name='calculate_total_days')
def calculate_total_days(parcel):
    """
    Calculates the total inclusive days between the first and last tracking log for a parcel.
    Assumes tracking_logs are prefetched and ordered by '-timestamp'.
    """
    if not parcel or not hasattr(parcel, 'tracking_logs') or not parcel.tracking_logs.all():
        return "N/A"

    logs = parcel.tracking_logs.all()
    # The default ordering on the model is '-timestamp', so the first element is the latest.
    last_log = logs[0]
    # The last element is the earliest.
    first_log = logs[len(logs)-1]

    if first_log and last_log and first_log.timestamp and last_log.timestamp:
        # Normalize to just the date part to count calendar days
        start_date = first_log.timestamp.date()
        end_date = last_log.timestamp.date()

        delta = end_date - start_date

        # Add 1 to make it inclusive. E.g., Mon to Mon is 1 day.
        total_days = delta.days + 1

        return f"{total_days} day{'s' if total_days != 1 else ''}"

    return "N/A"

@register.filter
def sum(dictionary_values):
    return builtins.sum(dictionary_values)
