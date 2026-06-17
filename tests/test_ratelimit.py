"""Rate-limiter robustness — the per-IP path must survive duplicate counter rows.

The `rate_limit` unique constraint is (key_id, window_start) and excludes
`bucket`; Postgres also treats NULL key_ids as distinct. So concurrent per-IP
requests (key_id=None) can race in duplicate rows for the same bucket+window.
The limiter must tolerate that instead of raising MultipleResultsFound (which was
500-ing the public website feed in production).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.ratelimit import _minute_window, limiter
from app.models import RateLimit


def test_per_ip_limiter_tolerates_duplicate_rows(db):
    window = _minute_window(datetime.now(timezone.utc))
    # Simulate the race: two NULL-key_id rows for the same ip bucket + window.
    db.add(RateLimit(key_id=None, bucket="ip:1.2.3.4", window_start=window, count=1))
    db.add(RateLimit(key_id=None, bucket="ip:1.2.3.4", window_start=window, count=1))
    db.commit()

    # Must not raise sqlalchemy.exc.MultipleResultsFound.
    limiter.check_request(db, key_id=None, ip="1.2.3.4")
