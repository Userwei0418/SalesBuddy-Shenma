"""Shanghai business-date windows shared by customer and opportunity views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

ActivityWindow = Literal["today", "day", "3d", "week", "month", "year"]
MapActivityWindow = Literal["today", "day", "3d", "week", "month", "quarter", "year", "all"]


@dataclass(frozen=True)
class ActivityWindowBounds:
    """Inclusive business-date bounds for a formal follow-up query."""

    key: str
    start: date
    end: date


@dataclass(frozen=True)
class MapActivityWindowBounds:
    key: str
    start: date | None
    end: date | None


def map_activity_window_bounds(as_of: date, window: str | None = None) -> MapActivityWindowBounds:
    """Map-only expansion; other activity-filter consumers retain their contract."""
    if window == "all":
        return MapActivityWindowBounds(key="all", start=None, end=None)
    if window == "quarter":
        return MapActivityWindowBounds(key="quarter", start=date(as_of.year, ((as_of.month - 1) // 3) * 3 + 1, 1), end=as_of)
    bounds = activity_window_bounds(as_of, window)
    return MapActivityWindowBounds(key=bounds.key, start=bounds.start, end=bounds.end)


def activity_window_bounds(as_of: date, window: str | None = None) -> ActivityWindowBounds:
    """Return an inclusive Shanghai business-date window ending on ``as_of``.

    ``None`` and ``year`` preserve the historical customer-map behaviour.  The
    short ``day`` spelling is accepted for UI clients; responses use ``today``.
    """
    key = "year" if window in (None, "year") else ("today" if window == "day" else window)
    if key not in {"today", "3d", "week", "month", "year"}:
        raise ValueError("活动时间范围只能是 today、3d、week、month 或 year")
    if key == "today":
        start = as_of
    elif key == "3d":
        start = as_of - timedelta(days=2)
    elif key == "week":
        start = as_of - timedelta(days=as_of.weekday())
    elif key == "month":
        start = as_of.replace(day=1)
    else:
        start = date(as_of.year, 1, 1)
    return ActivityWindowBounds(key=key, start=start, end=as_of)
