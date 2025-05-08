from django import template

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
