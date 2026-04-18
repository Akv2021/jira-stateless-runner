"""Integration tests for runner.rules.rule1_unit_created (M5).

Per docs/ImplementationRoadmap.md §M5 deliverables the four required
paths are: Difficulty-present happy path, Difficulty-missing fallback,
Stage-missing silent skip, and idempotent replay. Additional coverage
asserts the non-creation / non-Unit-issue filters short-circuit before
any Jira call.

Tests drive ``JiraClient`` through ``pytest-httpx``; the hidden
``count_issues`` Jira search that ``has_been_applied`` performs is
replaced by an injected async stub so tests don't need to mount the
``/rest/api/3/search`` endpoint on every case.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from tenacity import wait_none

from runner.config import get_settings
from runner.jira_client import JiraClient
from runner.models import ChangelogEvent
from runner.rules import UNIT_ISSUE_TYPES, rule1_unit_created

BASE_URL = "https://example.atlassian.net"
_SEARCH_URL_RE = re.compile(r"https://example\.atlassian\.net/rest/api/3/search.*")
UNIT_KEY = "PROJ-42"
BASE_ENV: dict[str, str] = {
    "JIRA_URL": BASE_URL,
    "JIRA_USER": "alice@example.com",
    "JIRA_TOKEN": "s3cret-token",
    "JIRA_PROJECT_KEY": "PROJ",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _env_and_fast_retry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    monkeypatch.setattr(JiraClient._request.retry, "wait", wait_none())  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()


def _unit_payload(stage: str | None, difficulty: str | None, summary: str) -> dict[str, Any]:
    fields: dict[str, Any] = {"summary": summary}
    if stage is not None:
        fields["Stage"] = {"value": stage}
    if difficulty is not None:
        fields["Difficulty"] = {"value": difficulty}
    return {"key": UNIT_KEY, "fields": fields}


def _event(*, is_new: bool = True, issuetype: str = "Problem", eid: int = 12345) -> ChangelogEvent:
    return ChangelogEvent(
        id=eid,
        issue_key=UNIT_KEY,
        created=datetime.fromisoformat("2026-04-18T12:00:00+00:00"),
        issuetype=issuetype,
        is_new_issue=is_new,
    )


def _mock_no_replay(httpx_mock: HTTPXMock) -> None:
    """Mount a permissive search mock so has_been_applied returns False."""
    httpx_mock.add_response(
        url=_SEARCH_URL_RE,
        json={"total": 0, "issues": []},
    )


def _mock_side_effects(httpx_mock: HTTPXMock) -> None:
    """Mount the three write endpoints Rule 1 invokes on a fresh run."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue", method="POST", json={"key": "PROJ-43"}
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}",
        method="PUT",
        status_code=204,
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}/comment",
        method="POST",
        json={"id": "10001"},
    )


@pytest.mark.anyio
async def test_rule1_happy_path_seeds_learn_subtask(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}",
        json=_unit_payload("Intermediate", "Medium", "Two-pointer traversal"),
    )
    _mock_no_replay(httpx_mock)
    _mock_side_effects(httpx_mock)
    async with JiraClient() as client:
        result = await rule1_unit_created(_event(), client, run_id=7241)
    assert result == "T1"
    subtask_post = next(
        r for r in httpx_mock.get_requests() if r.method == "POST" and r.url.path.endswith("/issue")
    )
    body = json.loads(subtask_post.content)["fields"]
    assert body["summary"] == "[Intermediate][Learn] \u2014 Two-pointer traversal"
    assert body["parent"] == {"key": UNIT_KEY}
    assert any(lbl.startswith("idem:") for lbl in body["labels"])
    put = next(r for r in httpx_mock.get_requests() if r.method == "PUT")
    assert json.loads(put.content)["fields"] == {"Revision Target": 3}


@pytest.mark.anyio
async def test_rule1_difficulty_fallback_defaults_target_to_two(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}",
        json=_unit_payload("Beginner", None, "Binary search"),
    )
    _mock_no_replay(httpx_mock)
    _mock_side_effects(httpx_mock)
    async with JiraClient() as client:
        result = await rule1_unit_created(_event(), client, run_id=7241)
    assert result == "T1"
    put = next(r for r in httpx_mock.get_requests() if r.method == "PUT")
    assert json.loads(put.content)["fields"] == {"Revision Target": 2}
    comment = next(r for r in httpx_mock.get_requests() if r.url.path.endswith("/comment"))
    body_doc = json.loads(comment.content)["body"]
    nodes = body_doc["content"][0]["content"]
    text = "".join(n.get("text", "") for n in nodes if n["type"] == "text")
    assert "Difficulty missing at creation; RevisionTarget defaulted to 2 (Easy)." in text


@pytest.mark.anyio
async def test_rule1_stage_missing_is_silent_skip(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}",
        json=_unit_payload(None, "Medium", "No stage"),
    )
    async with JiraClient() as client:
        result = await rule1_unit_created(_event(), client, run_id=1)
    assert result is None
    assert not any(r.method in {"POST", "PUT"} for r in httpx_mock.get_requests())


@pytest.mark.anyio
async def test_rule1_replay_is_noop(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}",
        json=_unit_payload("Advanced", "Hard", "Replay"),
    )
    httpx_mock.add_response(
        url=_SEARCH_URL_RE,
        json={"total": 1, "issues": [{"key": "PROJ-43"}]},
    )
    async with JiraClient() as client:
        result = await rule1_unit_created(_event(), client, run_id=1)
    assert result is None
    assert not any(r.method in {"POST", "PUT"} for r in httpx_mock.get_requests())


@pytest.mark.parametrize(
    ("is_new", "issuetype"),
    [(False, "Problem"), (True, "Sub-task"), (True, "Epic")],
)
@pytest.mark.anyio
async def test_rule1_filters_out_non_unit_creation_events(
    is_new: bool, issuetype: str, httpx_mock: HTTPXMock
) -> None:
    async with JiraClient() as client:
        result = await rule1_unit_created(
            _event(is_new=is_new, issuetype=issuetype), client, run_id=1
        )
    assert result is None
    assert httpx_mock.get_requests() == []


def test_unit_issue_types_matches_spec() -> None:
    expected = frozenset({"Problem", "Concept", "Implementation", "Pattern", "Debug"})
    assert expected == UNIT_ISSUE_TYPES
