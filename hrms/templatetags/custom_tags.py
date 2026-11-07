from django import template

register = template.Library()


@register.filter
def dict_key(value, arg):
    """
    Returns the value from a dictionary.
    If the dictionary or the key doesn't exist, it returns None safely.
    """
    if value is None:
        return None

    # Check if the object has a .get() method (is a dictionary)
    if hasattr(value, 'get'):
        return value.get(arg)

    return None