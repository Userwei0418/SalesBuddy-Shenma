"""Shanghai business dates for model facts; database instants remain timezone-aware."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = "Asia/Shanghai"
BUSINESS_TZ = ZoneInfo(BUSINESS_TIMEZONE)


def business_datetime(value):
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Human-entered timestamps without an offset belong to the business timezone.
    if value.tzinfo is None:
        value = value.replace(tzinfo=BUSINESS_TZ)
    return value.astimezone(BUSINESS_TZ)


def localize_business_times(value):
    """Keep native datetimes usable by asyncpg while serializing with +08:00."""
    if isinstance(value, datetime):
        return business_datetime(value)
    if isinstance(value, dict):
        result = {key: localize_business_times(item) for key, item in value.items()}
        if result.get("interaction_at") is not None:
            result["visit_date"] = business_datetime(result["interaction_at"]).date().isoformat()
        return result
    if isinstance(value, list):
        return [localize_business_times(item) for item in value]
    return value


def business_json_default(value):
    if isinstance(value, datetime):
        return business_datetime(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
