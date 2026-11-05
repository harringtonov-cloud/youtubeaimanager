"""Publishing-slot arithmetic (pure functions, stdlib only)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _parse(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def slots_between(start: datetime, end: datetime, publish_times: list[str], tz_name: str) -> list[datetime]:
    """All daily publish slots in [start, end), returned as UTC datetimes, ascending."""
    tz = ZoneInfo(tz_name)
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    day: date = start.astimezone(tz).date() - timedelta(days=1)
    last: date = end.astimezone(tz).date() + timedelta(days=1)
    out: list[datetime] = []
    while day <= last:
        for t in publish_times:
            local = datetime.combine(day, _parse(t), tzinfo=tz)
            utc = local.astimezone(timezone.utc)
            if start <= utc < end:
                out.append(utc)
        day += timedelta(days=1)
    return sorted(set(out))


def next_slots(now: datetime, days: int, publish_times: list[str], tz_name: str,
               min_lead_minutes: int = 30) -> list[datetime]:
    """Upcoming slots within `days`, skipping ones that are too close to produce."""
    earliest = now + timedelta(minutes=min_lead_minutes)
    return slots_between(earliest, now + timedelta(days=days), publish_times, tz_name)
