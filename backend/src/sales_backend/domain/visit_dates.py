"""Date-only visit presentation in the business timezone."""

from datetime import date, datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def business_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=BEIJING).date() if value.tzinfo is None else value.astimezone(BEIJING).date()
    if isinstance(value, date):
        return value
    try:
        return business_date(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def within_seven_days(value, today=None):
    visit_date = business_date(value)
    if visit_date is None:
        return None
    today = business_date(today) if today is not None else datetime.now(BEIJING).date()
    return 0 <= (today - visit_date).days <= 6


def visit_date_fields(row):
    item = dict(row)
    visit_date = business_date(item.get("interaction_at"))
    created_date = business_date(item.get("recorded_on") or item.get("created_at"))
    return {
        **item,
        "visit_date": visit_date.isoformat() if visit_date else None,
        "created_date": created_date.isoformat() if created_date else None,
        "within_seven_days": within_seven_days(visit_date),
    }
