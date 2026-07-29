import pyodbc
from datetime import timedelta
from django.utils import timezone
from .models import PurchaseOrderStage

def fetch_tally_stock():
    conn_str = (
        r"DSN=TallyODBC64_9000;"
    )
    try:
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        query = "SELECT $Name, $ClosingBalance, $BaseUnits FROM StockItem"
        cursor.execute(query)

        result = {}
        for row in cursor.fetchall():
            name = row[0] if row[0] else ''
            balance = row[1]
            unit = row[2]
            result[name] = {
                "balance": balance,
                "unit": unit
            }
        return result
    except Exception as e:
        print("Error:", e)
        return []


def get_current_stage(po):
    """
    Returns the active stage of a Purchase Order.
    """
    return (
        po.stage_logs
        .filter(exit_datetime__isnull=True)
        .select_related("stage")
        .first()
    )


def get_remaining_stages(po):
    """
    Returns all stages after the current stage.
    """
    current = get_current_stage(po)

    if not current:
        return PurchaseOrderStage.objects.none()

    return PurchaseOrderStage.objects.filter(
        is_active=True,
        sort_order__gt=current.stage.sort_order
    ).order_by("sort_order")


def get_days_in_current_stage(po):
    """
    Returns number of days spent in current stage.
    """
    current = get_current_stage(po)

    if not current:
        return 0

    return current.days_at_stage


def get_remaining_days(po):
    """
    Returns estimated remaining days until arrival.
    """
    current = get_current_stage(po)

    if not current:
        return 0

    remaining = max(
        current.stage.estimated_days - float(current.days_at_stage),
        0
    )

    for stage in get_remaining_stages(po):
        remaining += stage.estimated_days

    return remaining


def get_expected_arrival_date(po):
    current = get_current_stage(po)

    if not current:
        return None

    remaining = get_remaining_days(po)

    return current.entered_at.date() + timedelta(
        days=float(current.days_at_stage) + float(remaining)
    )