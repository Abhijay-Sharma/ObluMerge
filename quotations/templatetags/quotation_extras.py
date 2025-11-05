from django import template

register = template.Library()

@register.filter
def altered_price(item, altered_prices):
    """
    Template filter to return the altered price for an item if it exists,
    otherwise the item's original price.
    Usage in template:
        {{ item|altered_price:altered_prices }}
    """
    if not altered_prices:
        return item.unit_price_without_tax
    return altered_prices.get(str(item.id), item.unit_price_without_tax)


@register.filter
def get_item(dictionary, key):
    """
    Allows templates to access dictionary values dynamically:
        {{ my_dict|get_item:my_key }}
    """
    if dictionary is None:
        return None
    return dictionary.get(str(key)) or dictionary.get(key)

@register.filter
def safe_number(value):
    """
    Converts value to a float if possible, else returns 0.
    Prevents 'widthratio' and math filters from failing.
    """
    try:
        if value in (None, '', 'None'):
            return 0
        return float(value)
    except (ValueError, TypeError):
        return 0

@register.filter
def multiply(value, arg):
    """Multiplies two numbers safely"""
    try:
        return float(value) * float(arg)
    except (TypeError, ValueError):
        return 0

@register.filter
def divide(value, arg):
    """Divides two numbers safely, returns 0 if invalid or divide-by-zero"""
    try:
        arg = float(arg)
        if arg == 0:
            return 0
        return float(value) / arg
    except (TypeError, ValueError):
        return 0

@register.filter
def add(value, arg):
    """Safely add two numbers."""
    try:
        return float(value) + float(arg)
    except (TypeError, ValueError):
        return value or 0

@register.filter
def subtract(value, arg):
    """
    Subtracts the given arg from value.
    Usage: {{ value|subtract:arg }}
    Example: {{ 10|subtract:4 }} → 6
    """
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return ''