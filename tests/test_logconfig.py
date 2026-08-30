"""Structured (JSON) logging configuration."""

from __future__ import annotations

import json
import logging

from app.core.logconfig import JsonFormatter, configure_logging


def _record(**extra):
    rec = logging.LogRecord(
        name="dsec.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_json_formatter_basic_fields():
    line = JsonFormatter().format(_record())
    obj = json.loads(line)
    assert obj["level"] == "INFO"
    assert obj["logger"] == "dsec.test"
    assert obj["message"] == "hello world"  # %-args rendered
    assert "ts" in obj


def test_json_formatter_includes_extras():
    line = JsonFormatter().format(_record(request_id="abc", count=3))
    obj = json.loads(line)
    assert obj["request_id"] == "abc"
    assert obj["count"] == 3


def test_json_formatter_non_serialisable_extra_is_repr():
    line = JsonFormatter().format(_record(weird=object()))
    obj = json.loads(line)  # must still be valid JSON
    assert "weird" in obj


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = _record()
        rec.exc_info = sys.exc_info()
        obj = json.loads(JsonFormatter().format(rec))
    assert "boom" in obj["exc"]


def test_configure_logging_json_sets_formatter():
    configure_logging(log_format="json", level="DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_configure_logging_is_idempotent():
    configure_logging(log_format="text", level="INFO")
    configure_logging(log_format="text", level="INFO")
    # Replaces rather than stacks handlers.
    assert len(logging.getLogger().handlers) == 1
    assert not isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)


def test_configure_logging_bad_level_defaults_info():
    configure_logging(log_format="text", level="NOPE")
    assert logging.getLogger().level == logging.INFO
