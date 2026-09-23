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
