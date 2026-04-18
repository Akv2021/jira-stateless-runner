"""Tests for runner.idempotency and runner.audit.

Two concerns grouped here per the M4 test mandate:

1. **Replay-safety** — identical ``(unit, event, transition)`` inputs
   produce identical keys, and ``has_been_applied`` short-circuits when
   the injected ``count`` callable returns a non-zero total.
2. **Audit-comment byte-exactness** — ``format_comment`` emits the §5.2
   canonical template verbatim (arrow, middle-dot, indentation, and
   ``idem_<hex>`` display form).
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pytest

from runner.audit import TransitionEvent, format_comment
from runner.idempotency import (
    IDEM_DISPLAY_PREFIX,
    IDEM_LABEL_NAMESPACE,
    compute_key,
    display_for,
    has_been_applied,
    label_for,
    replay_jql,
)
from runner.models import Unit

_UNIT = Unit(
    key="PROJ-1",
    stage="Beginner",
    work_type="Learn",
    lifecycle="Active",
    revision_done=0,
    revision_target=3,
)


def test_compute_key_matches_sha256_prefix() -> None:
    raw = "PROJ-1|12345678|T2"
    expected = hashlib.sha256(raw.encode()).hexdigest()[:12]
    assert compute_key("PROJ-1", "12345678", "T2") == expected
    assert len(compute_key("PROJ-1", "12345678", "T2")) == 12


def test_compute_key_is_deterministic_under_replay() -> None:
    k1 = compute_key("PROJ-1", "12345678", "T2")
    k2 = compute_key("PROJ-1", "12345678", "T2")
    assert k1 == k2


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (("PROJ-1", "1", "T2"), ("PROJ-1", "1", "T3")),
        (("PROJ-1", "1", "T2"), ("PROJ-1", "2", "T2")),
        (("PROJ-1", "1", "T2"), ("PROJ-2", "1", "T2")),
    ],
)
def test_compute_key_changes_on_any_input_change(
    a: tuple[str, str, str], b: tuple[str, str, str]
) -> None:
    assert compute_key(a[0], a[1], a[2]) != compute_key(b[0], b[1], b[2])  # type: ignore[arg-type]


def test_label_for_is_namespaced_colon_form() -> None:
    assert label_for("8c4d2a1f9bb7") == "idem:8c4d2a1f9bb7"
    assert IDEM_LABEL_NAMESPACE == "idem"


def test_display_for_uses_underscore_prefix() -> None:
    assert display_for("8c4d2a1f9bb7") == "idem_8c4d2a1f9bb7"
    assert IDEM_DISPLAY_PREFIX == "idem_"


def test_replay_jql_shape() -> None:
    jql = replay_jql(_UNIT, "abc123def456")
    assert jql == 'parent = "PROJ-1" AND labels = "idem:abc123def456"'


@pytest.mark.anyio
async def test_has_been_applied_true_on_existing_label() -> None:
    captured: dict[str, Any] = {}

    async def count(jql: str) -> int:
        captured["jql"] = jql
        return 1

    assert await has_been_applied(_UNIT, "abc123def456", count) is True
    assert captured["jql"] == 'parent = "PROJ-1" AND labels = "idem:abc123def456"'


@pytest.mark.anyio
async def test_has_been_applied_false_on_zero_total() -> None:
    async def count(_jql: str) -> int:
        return 0

    assert await has_been_applied(_UNIT, "abc123def456", count) is False


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- §5.2 audit-comment byte-for-byte test ---------------------------------

_T2_EVENT = TransitionEvent(
    transition_id="T2",
    source_label="Learn#1",
    target_label="Revise#1",
    revision_done_pre=0,
    revision_done_post=0,
    revision_target=3,
    outcome="Pass",
    due_date=date(2026, 4, 22),
    gap_index=1,
    gap_bd=2,
    run_id=7241,
    event_id=12345678,
    key="8c4d2a1f9bb7",
)

_EXPECTED_T2_COMMENT = (
    "[ZTMOS][T2] Learn#1 \u2192 Revise#1\n"
    "  RevisionDone: 0 \u2192 0 (target 3)\n"
    "  Outcome: Pass\n"
    "  DueDate(Revise#1): 2026-04-22  (RevisionGap[1] = 2bd)\n"
    "  run: 7241 \u00b7 event: 12345678 \u00b7 key: idem_8c4d2a1f9bb7"
)


def test_format_comment_matches_spec_byte_for_byte() -> None:
    assert format_comment(_T2_EVENT) == _EXPECTED_T2_COMMENT


def test_format_comment_omits_optional_lines_when_absent() -> None:
    minimal = TransitionEvent(
        transition_id="T8",
        source_label="Active",
        target_label="Paused",
        revision_done_pre=1,
        revision_done_post=1,
        revision_target=3,
        run_id=99,
        event_id="evt-1",
        key="0" * 12,
    )
    rendered = format_comment(minimal)
    assert "Outcome" not in rendered
    assert "DueDate" not in rendered
    assert rendered.endswith("key: idem_000000000000")


def test_format_comment_is_deterministic_on_replay() -> None:
    assert format_comment(_T2_EVENT) == format_comment(_T2_EVENT)
