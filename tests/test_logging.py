"""Privacy allow-list regression for runner.logging_ext (M9, §8.3).

The allow-list is the authoritative policy: fields not on the allow-list
are dropped from INFO payloads, even when the caller passes them via
``extra=``. DEBUG / WARN / ERROR records preserve the full payload.
"""

from __future__ import annotations

import json
import logging

import pytest

from runner.logging_ext import StructuredFormatter, get_logger


@pytest.fixture
def formatter() -> StructuredFormatter:
    return StructuredFormatter()


def _make_record(
    *,
    level: int = logging.INFO,
    msg: str = "dispatch_ok",
    extras: dict[str, object] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="runner.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in (extras or {}).items():
        setattr(record, k, v)
    return record


def test_info_record_drops_summary_field(formatter: StructuredFormatter) -> None:
    record = _make_record(
        extras={"unit": "PROJ-1", "summary": "SECRET interview topic", "transition": "T2"}
    )
    payload = json.loads(formatter.format(record))
    assert "summary" not in payload
    assert payload["unit"] == "PROJ-1"
    assert payload["transition"] == "T2"


def test_info_record_drops_description_and_comment(formatter: StructuredFormatter) -> None:
    record = _make_record(
        extras={
            "unit": "PROJ-1",
            "description": "long markdown body",
            "comment": "oh no a private note",
        }
    )
    payload = json.loads(formatter.format(record))
    assert "description" not in payload
    assert "comment" not in payload


def test_debug_record_retains_full_payload(formatter: StructuredFormatter) -> None:
    record = _make_record(
        level=logging.DEBUG,
        extras={"unit": "PROJ-1", "summary": "debug-time full context"},
    )
    payload = json.loads(formatter.format(record))
    assert payload["summary"] == "debug-time full context"


def test_error_record_retains_full_payload(formatter: StructuredFormatter) -> None:
    record = _make_record(
        level=logging.ERROR,
        msg="fatal",
        extras={"unit": "PROJ-1", "summary": "explain why", "transition": "T2"},
    )
    payload = json.loads(formatter.format(record))
    assert payload["lvl"] == "ERROR"
    assert payload["summary"] == "explain why"


def test_allowed_fields_are_passed_through(formatter: StructuredFormatter) -> None:
    extras = {
        "unit": "PROJ-1",
        "subtask": "PROJ-2",
        "transition": "T3",
        "idem_key": "abc123",
        "stage": "Intermediate",
        "work_type": "Revise",
        "lifecycle": "Active",
        "rev_done": 1,
        "rev_target": 3,
        "outcome": "Pass",
        "due": "2026-04-22",
        "event_id": 12345,
    }
    record = _make_record(extras=extras)
    payload = json.loads(formatter.format(record))
    for key, value in extras.items():
        assert payload[key] == value


def test_get_logger_attaches_handler_once() -> None:
    logger = get_logger("runner.test.singleton")
    handlers_first = list(logger.handlers)
    logger2 = get_logger("runner.test.singleton")
    assert logger is logger2
    assert logger.handlers == handlers_first
