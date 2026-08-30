"""The club's wall clock.

DSEC is a Deakin University club: every member, event and DUSA report is on
Melbourne time. Server processes run in UTC (Vercel does, and so does the VPS),
so any date derived from the server's own clock is wrong for up to 11 hours a
day. Route every *calendar date* decision through here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

MELBOURNE = ZoneInfo("Australia/Melbourne")


def now_local() -> datetime:
    """The current instant, expressed in Melbourne time."""
    return datetime.now(MELBOURNE)


def today_local() -> date:
    """Today's calendar date in Melbourne."""
    return now_local().date()


def local_date(dt: datetime) -> date:
    """The Melbourne calendar date an instant falls on.

    A naive datetime is assumed to be UTC, which is what every naive value in
    this codebase actually is.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MELBOURNE).date()
