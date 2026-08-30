"""Process-wide stdlib logging configuration (console vs. JSON log drains).

Separate from ``app.core.logging`` — that module writes the *EventLog* audit
trail into the database; this one configures Python's ``logging`` handlers that
emit to stdout/stderr, which is what a host log drain (Vercel, the VPS journal,
an aggregator) actually ingests.

``LOG_FORMAT=json`` swaps the console formatter for one line of JSON per record,
so a drain can parse level/logger/message plus any structured ``extra=`` fields
without regex. ``LOG_FORMAT=text`` (the default) keeps the readable console
format, so importing and calling ``configure_logging()`` is a no-op change until
an operator opts in.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

# LogRecord attributes that are intrinsic to every record; anything NOT in here
# was attached by the caller via ``logger.info(..., extra={...})`` and is worth
# surfacing as a structured field in the JSON line.
_STANDARD_RECORD_FIELDS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # Structured extras passed via logger.<level>(..., extra={...}).
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)  # only keep JSON-serialisable extras
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, log_format: str = "text", level: str = "INFO") -> None:
    """Configure the root logger's single stream handler.

    Idempotent: replaces the root handlers rather than appending, so a re-import
    (or a test re-running app startup) does not stack duplicate handlers the way
    ``logging.basicConfig`` silently would.
    """
    root = logging.getLogger()
    root.setLevel(_coerce_level(level))

    handler = logging.StreamHandler()
    if (log_format or "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )

    # Replace, don't append — the swap-point that keeps handlers from stacking.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)


def _coerce_level(level: str) -> int:
    """Map a level name to its numeric value, defaulting to INFO on garbage."""
    resolved = logging.getLevelName((level or "INFO").upper())
    return resolved if isinstance(resolved, int) else logging.INFO
