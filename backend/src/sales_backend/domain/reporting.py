from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")


def quarter_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Shanghai quarter boundaries; explicit instants independent of DB timezone."""
    instant = now or datetime.now(ZONE)
    if instant.tzinfo is None:
        raise ValueError("季度统计时间必须包含时区")
    local = instant.astimezone(ZONE)
    month = (local.month - 1) // 3 * 3 + 1
    start = datetime(local.year, month, 1, tzinfo=ZONE)
    end = datetime(local.year + (month == 10), 1 if month == 10 else month + 3, 1, tzinfo=ZONE)
    return start, end


def reporting_window(period="month", start: date | None = None, end: date | None = None, *, now=None):
    today = (now or datetime.now(ZONE)).astimezone(ZONE).date()
    if period == "custom":
        if not start or not end:
            raise ValueError("请选择开始与结束日期")
    elif period == "day":
        start, end = today, today
    elif period == "week":
        start, end = today - timedelta(days=today.weekday()), today
    elif period == "month":
        start, end = today.replace(day=1), today
    else:
        raise ValueError("统计周期不正确")
    if end < start or (end - start).days > 366:
        raise ValueError("请选择一年以内的有效日期范围")
    return (
        datetime.combine(start, time.min, ZONE).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, ZONE).astimezone(UTC),
    )
