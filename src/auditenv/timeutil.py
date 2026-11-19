"""Company time zones with fixed UTC offsets.

Only zones without DST in the simulated years are used, so no tz database is needed
and results never depend on the installed tzdata version.
"""

from datetime import date, datetime, timedelta, timezone

ZONE_OFFSETS_MINUTES = {
    "UTC": 0,
    "Asia/Almaty": 5 * 60,
    "Asia/Dubai": 4 * 60,
    "Asia/Kolkata": 5 * 60 + 30,
    "Asia/Tokyo": 9 * 60,
    "America/Bogota": -5 * 60,
}


def tzinfo_for(zone: str) -> timezone:
    return timezone(timedelta(minutes=ZONE_OFFSETS_MINUTES[zone]))


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp without offset")
    return dt


def local_date(dt: datetime, zone: str) -> date:
    return dt.astimezone(tzinfo_for(zone)).date()


def days_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - date(year, month, 1)).days
