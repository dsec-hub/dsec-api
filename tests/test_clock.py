"""The club's wall clock (app/core/clock.py).

The key production risk is import-time resolution of the Melbourne zone: the
runtime image has no system zoneinfo, so the `tzdata` package must supply it or
the whole API fails to boot. These tests fail if the zone can't be resolved.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.core import clock


def test_melbourne_zone_resolves():
    # Would raise ZoneInfoNotFoundError if neither a system tz db nor the tzdata
    # package is present — the exact boot failure this guards against.
    assert clock.MELBOURNE.key == "Australia/Melbourne"


def test_local_date_crosses_utc_boundary_in_winter():
    # 2026-06-11 22:00 UTC is 2026-06-12 08:00 in Melbourne (AEST, UTC+10).
    dt = datetime(2026, 6, 11, 22, 0, tzinfo=timezone.utc)
    assert clock.local_date(dt) == date(2026, 6, 12)


def test_local_date_honours_daylight_saving():
    # 2026-01-11 14:00 UTC is 2026-01-12 01:00 in Melbourne (AEDT, UTC+11) —
    # proves ZoneInfo (not a hardcoded offset) is driving the conversion.
    dt = datetime(2026, 1, 11, 14, 0, tzinfo=timezone.utc)
    assert clock.local_date(dt) == date(2026, 1, 12)


def test_local_date_treats_naive_as_utc():
    naive = datetime(2026, 6, 11, 22, 0)  # no tzinfo → assumed UTC
    assert clock.local_date(naive) == date(2026, 6, 12)
