from django import template
from django.utils import timezone
import pprint

register = template.Library()

@register.filter
def dict_get(dictionary, key):
    """Get a value from a dictionary in a template using dot notation"""
    try:
        return dictionary.get(key)
    except (AttributeError, TypeError):
        return None

@register.filter
def index(sequence, item):
    """
    用于获取 item 在 sequence 中的位置（0-based index）
    如果找不到，返回 -1
    """
    try:
        return sequence.index(item)
    except ValueError:
        return -1

@register.filter(name='badge_class')
def badge_class(status):
    """Returns classes for a softer 'pill' style status indicator."""
    # Note: These use arbitrary Tailwind CSS colors. You can customize them.
    # The 'badge' class from DaisyUI is removed to allow for custom padding and styling.
    class_map = {
        'DELIVERED': 'px-1 py-1 rounded-full text-green-800 bg-green-100',
        'PARTIALLY_DELIVERED': 'px-1 py-1 rounded-full text-purple-800 bg-purple-100',
        'PAYMENT_MADE': 'px-1 py-1 rounded-full text-blue-800 bg-blue-100',
        'WAITING_INVOICE': 'px-1 py-1 rounded-full text-yellow-800 bg-yellow-100',
        'CANCELLED': 'px-1 py-1 rounded-full text-red-800 bg-red-100',
        'DRAFT': 'px-1 py-1 rounded-full text-gray-800 bg-gray-100',
    }
    return class_map.get(status, 'badge-ghost')

@register.filter(name='stringformat')
def stringformat(value, fmt):
    """
    Allows string formatting in templates, e.g., {{ my_int|stringformat:'s' }}
    This is useful for comparing numbers and strings in template logic.
    """
    return str(value)

@register.filter(name='eta_color_class')
def eta_color_class(eta):
    """Returns a CSS class if the ETA date is in the past."""
    if eta and eta < timezone.now().date():
        return "text-red-500 font-semibold"
    return ""


@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Allows accessing a dictionary key with a variable in Django templates.
    This version correctly handles both integer and string keys.
    """
    if isinstance(dictionary, dict):
        # CORRECTED: Convert the key to a string before looking it up
        return dictionary.get(str(key))
    return None

@register.filter(name='pprint')
def pretty_print(value):
    """
    A 'pretty print' filter for debugging dictionaries in templates.
    The function is renamed to avoid a name collision with the imported pprint module.
    """
    return pprint.pformat(value)

@register.filter(name='multiply')
def multiply(value, arg):
    """Multiplies the given value by the argument."""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        # Return an empty string if values are not numbers
        return ''
