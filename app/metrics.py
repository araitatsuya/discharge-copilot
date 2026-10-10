"""Small number helpers that never crash on None, empty input or zero."""
from datetime import datetime


def safe_divide(numerator: float | None, denominator: float | None, default: float = 0.0) -> float:
    if numerator is None or not denominator:
        return default
    return numerator / denominator


def occupancy_rate(occupied: int, total: int) -> float:
    """Fraction of beds in use, 0.0..1.0. Zero beds -> 0.0, not ZeroDivisionError."""
    return safe_divide(occupied, total)


def to_naive(dt: datetime | None) -> datetime | None:
    """Convert "2026-10-09T08:40:00Z"-style aware times to naive local time.

    Comparing aware and naive datetimes raises TypeError, so everything we store
    or compare goes through here.
    """
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


def length_of_stay_days(admit_at: datetime | None, now: datetime | None = None) -> float | None:
    """Days since admission, rounded to 0.1. None if we don't know when they were admitted.

    An admit time in the future (bad data / clock skew) counts as 0, never negative.
    """
    if admit_at is None:
        return None
    now = to_naive(now) or datetime.now()
    days = (now - to_naive(admit_at)).total_seconds() / 86400
    return round(max(days, 0.0), 1)


def hours_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max((to_naive(end) - to_naive(start)).total_seconds() / 3600, 0.0), 1)


def format_days(days: float | None) -> str:
    return "—" if days is None else f"{days:.1f} d"


def format_hours(hours: float | None) -> str:
    return "—" if hours is None else f"{hours:.1f} h"


def format_percent(fraction: float | None) -> str:
    return "—" if fraction is None else f"{fraction * 100:.0f}%"
