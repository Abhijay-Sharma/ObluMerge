import calendar

from django import template

register = template.Library()


@register.filter
def month_name(value):
    try:
        return calendar.month_name[int(value)]
    except (ValueError, TypeError, IndexError):
        return value


@register.filter
def sub(value, arg):
    """Subtracts arg from value"""
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return 0

@register.filter
def divide(value, arg):
    """Divides value by arg"""
    try:
        return float(value) / float(arg)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0

@register.filter
def multiply(value, arg):
    """Multiplies value by arg"""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0


