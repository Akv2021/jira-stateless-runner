"""Unit tests for runner.models Pydantic domain models.

Covers happy-path construction, field-level validation against the
LivingRequirements §2 vocabulary, immutability (frozen config), and
Jira-payload parsing via ``fromString``/``toString`` aliases on
``ChangelogItem``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from runner.models import (
    ChangelogEvent,
    ChangelogItem,
    Subtask,
    Unit,
)

_UNIT_BASE: dict[str, Any] = {
    "key": "PROJ-1",
    "stage": "Beginner",
    "work_type": "Learn",
    "lifecycle": "Active",
    "revision_done": 0,
    "revision_target": 2,
}

_SUBTASK_BASE: dict[str, Any] = {
    "key": "PROJ-2",
    "parent_key": "PROJ-1",
    "work_type": "Learn",
    "status": "Todo",
    "title": "[Beginner][Learn] — example",
}


def test_unit_happy_path() -> None:
    u = Unit(**_UNIT_BASE)
    assert u.key == "PROJ-1"
    assert u.stage == "Beginner"
    assert u.difficulty is None
    assert u.has_had_test is False
    assert u.last_subtask_completed_at is None


def test_unit_is_frozen() -> None:
    u = Unit(**_UNIT_BASE)
    with pytest.raises(ValidationError):
        u.revision_done = 5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stage", "beginner"),
        ("work_type", "Test"),
        ("lifecycle", "Inactive"),
        ("revision_done", -1),
        ("revision_target", 0),
        ("revision_target", 11),
        ("difficulty", "Trivial"),
    ],
)
def test_unit_field_validation(field: str, value: object) -> None:
    payload = {**_UNIT_BASE, field: value}
    with pytest.raises(ValidationError):
        Unit(**payload)


@pytest.mark.parametrize(
    "bad_key", ["proj-1", "1PROJ-1", "PROJ-0", "PROJ-01", "PROJ", "PROJ-", "-1"]
)
def test_unit_rejects_invalid_issue_key(bad_key: str) -> None:
    with pytest.raises(ValidationError):
        Unit(**{**_UNIT_BASE, "key": bad_key})


def test_subtask_accepts_test_work_type_and_outcome() -> None:
    s = Subtask(**{**_SUBTASK_BASE, "work_type": "Test", "status": "Done", "outcome": "Regress"})
    assert s.work_type == "Test"
    assert s.outcome == "Regress"


def test_subtask_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        Subtask(**{**_SUBTASK_BASE, "title": ""})


def test_subtask_rejects_negative_effort_points() -> None:
    with pytest.raises(ValidationError):
        Subtask(**{**_SUBTASK_BASE, "effort_points": -1})


def test_changelog_event_parses_jira_shape() -> None:
    event = ChangelogEvent.model_validate(
        {
            "id": 1001,
            "issue_key": "PROJ-1",
            "created": "2025-06-01T12:00:00+00:00",
            "author_account_id": "acc-xyz",
            "items": [
                {"field": "status", "fromString": "To Do", "toString": "Done"},
                {"field": "Lifecycle", "fromString": "Active", "toString": "Paused"},
            ],
        }
    )
    assert event.id == 1001
    assert event.issue_key == "PROJ-1"
    assert event.created == datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    assert len(event.items) == 2
    first: ChangelogItem = event.items[0]
    assert first.field == "status"
    assert first.from_value == "To Do"
    assert first.to_value == "Done"


def test_changelog_event_defaults_items_to_empty_tuple() -> None:
    event = ChangelogEvent(id=5, issue_key="PROJ-1", created=datetime.now(tz=UTC))
    assert event.items == ()
    assert event.author_account_id is None


def test_changelog_item_field_is_required() -> None:
    with pytest.raises(ValidationError):
        ChangelogItem.model_validate({"fromString": "x", "toString": "y"})
