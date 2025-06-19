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
