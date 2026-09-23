"""Calendar ranges shared by FDE facts, ranking and rhythm, in Beijing time."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ReportingPeriod:
    start: date
    end: date
    quarters: tuple[int, ...]
    kind: str

    @property
    def start_at(self):
        return datetime.combine(self.start, time.min, TZ)

    @property
    def end_at(self):
        return datetime.combine(self.end + timedelta(days=1), time.min, TZ)

    def contains(self, day):
        return self.start <= day <= self.end and (not self.quarters or (day.month - 1) // 3 + 1 in self.quarters)


def reporting_period(now, *, year=None, quarters=None, period=None, date_from=None, date_to=None):
    today = now.astimezone(TZ).date()
    year = year or today.year
    quarters = tuple(sorted(set(quarters or [])))
    if not 2000 <= year <= 2100 or any(q not in {1, 2, 3, 4} for q in quarters):
        raise ValueError("请选择有效年份和季度")
    if period not in {None, "week", "month", "quarter", "year", "all"}:
        raise ValueError("不支持该统计周期")
    if bool(date_from) != bool(date_to):
        raise ValueError("请同时提供开始日期和结束日期")
    if date_from:
        if date_from > date_to or (date_to - date_from).days > 3660:
            raise ValueError("统计日期范围无效")
        return ReportingPeriod(date_from, date_to, (), "custom")
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return ReportingPeriod(start, start + timedelta(days=6), (), period)
    if period == "month":
        start = today.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return ReportingPeriod(start, end, (), period)
    if period == "quarter" and not quarters:
        quarters = ((today.month - 1) // 3 + 1,)
    if period == "all":
        return ReportingPeriod(date(2000, 1, 1), today, (), period)
    start = date(year, (min(quarters) - 1) * 3 + 1, 1) if quarters else date(year, 1, 1)
    end_month = max(quarters) * 3 if quarters else 12
    end = date(year + 1, 1, 1) if end_month == 12 else date(year, end_month + 1, 1)
    return ReportingPeriod(start, end - timedelta(days=1), quarters, period or "year")


def rhythm_axes(rows, selected, now):
    values = {str(row["date"]): int(row["visits"]) for row in rows}
    end = min(now.astimezone(TZ).date(), selected.end)
    while end >= selected.start and not selected.contains(end):
        end -= timedelta(days=1)
    if end < selected.start:
        return [], []
    days = []
    for offset in range(6, -1, -1):
        day = end - timedelta(days=offset)
        days.append(
            {"date": day.isoformat(), "visits": values.get(day.isoformat(), 0) if selected.contains(day) else 0}
        )
    weeks = []
    monday = end - timedelta(days=end.weekday())
    for offset in range(11, -1, -1):
        start = monday - timedelta(weeks=offset)
        total = sum(
            values.get((start + timedelta(days=i)).isoformat(), 0)
            for i in range(7)
            if selected.contains(start + timedelta(days=i)) and start + timedelta(days=i) <= end
        )
        weeks.append(
            {"date": start.isoformat(), "end_date": min(start + timedelta(days=6), end).isoformat(), "visits": total}
        )
    return days, weeks
