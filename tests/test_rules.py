"""Integration tests for runner.rules.

Covers ``rule1_unit_created`` (M5) and ``rule2_subtask_done`` (M6).
Tests drive ``JiraClient`` through ``pytest-httpx``; the Jira search
``count_issues`` performs for ``has_been_applied`` is mounted via a
regex-matched response.
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
from runner.rules import (
    HAS_HAD_TEST_FIELD,
    STALE_ELIGIBLE_FILTER,
    UNIT_ISSUE_TYPES,
    rule1_unit_created,
    rule2_subtask_done,
    rule4_stale_scan,
)

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


# ---------------------------------------------------------------------------
# Rule 2 — Sub-task → Done dispatch (M6)
# ---------------------------------------------------------------------------

SUBTASK_KEY = "PROJ-99"
NEW_SUBTASK_KEY = "PROJ-100"
_R2_NOW = datetime.fromisoformat("2026-04-20T09:00:00+00:00")  # Monday


def _subtask_payload(
    *, work_type: str, outcome: str | None = None, parent_key: str = UNIT_KEY
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": "Subtask",
        "parent": {"key": parent_key},
        "Work Type": {"value": work_type},
    }
    if outcome is not None:
        fields["Outcome"] = {"value": outcome}
    return {"key": SUBTASK_KEY, "fields": fields}


def _unit_r2_payload(
    *, stage: str, work_type: str, lifecycle: str, rev_done: int, rev_target: int
) -> dict[str, Any]:
    return {
        "key": UNIT_KEY,
        "fields": {
            "summary": "Two Sum",
            "Stage": {"value": stage},
            "Work Type": {"value": work_type},
            "Lifecycle": {"value": lifecycle},
            "Revision Done": rev_done,
            "Revision Target": rev_target,
        },
    }


def _done_event(
    *, eid: int = 55555, issuetype: str = "Sub-task", is_done: bool = True
) -> ChangelogEvent:
    return ChangelogEvent(
        id=eid,
        issue_key=SUBTASK_KEY,
        created=datetime.fromisoformat("2026-04-20T09:00:00+00:00"),
        issuetype=issuetype,
        is_status_change_to_done=is_done,
    )


def _mock_r2_reads(httpx_mock: HTTPXMock, *, subtask: dict[str, Any], unit: dict[str, Any]) -> None:
    httpx_mock.add_response(url=f"{BASE_URL}/rest/api/3/issue/{SUBTASK_KEY}", json=subtask)
    httpx_mock.add_response(url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}", json=unit)


def _mock_r2_writes(httpx_mock: HTTPXMock, *, create_subtask: bool = True) -> None:
    if create_subtask:
        httpx_mock.add_response(
            url=f"{BASE_URL}/rest/api/3/issue",
            method="POST",
            json={"key": NEW_SUBTASK_KEY},
        )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}", method="PUT", status_code=204
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}/comment",
        method="POST",
        json={"id": "10099"},
    )


def _subtask_post_body(httpx_mock: HTTPXMock) -> dict[str, Any]:
    post = next(
        r for r in httpx_mock.get_requests() if r.method == "POST" and r.url.path.endswith("/issue")
    )
    body: dict[str, Any] = json.loads(post.content)["fields"]
    return body


def _put_body(httpx_mock: HTTPXMock) -> dict[str, Any]:
    put = next(r for r in httpx_mock.get_requests() if r.method == "PUT")
    body: dict[str, Any] = json.loads(put.content)["fields"]
    return body


@pytest.mark.anyio
async def test_rule2_t2_learn_done_seeds_revise_one(httpx_mock: HTTPXMock) -> None:
    _mock_r2_reads(
        httpx_mock,
        subtask=_subtask_payload(work_type="Learn"),
        unit=_unit_r2_payload(
            stage="Intermediate", work_type="Learn", lifecycle="Active", rev_done=0, rev_target=3
        ),
    )
    _mock_no_replay(httpx_mock)
    _mock_r2_writes(httpx_mock)
    async with JiraClient() as client:
        result = await rule2_subtask_done(_done_event(), client, run_id=1, now=_R2_NOW)
    assert result == "T2"
    body = _subtask_post_body(httpx_mock)
    assert body["summary"] == "[Intermediate][Revise#1] \u2014 Two Sum"
    assert body["duedate"] == "2026-04-22"  # Mon + 2bd = Wed
    assert any(lbl.startswith("idem:") for lbl in body["labels"])
    assert _put_body(httpx_mock)["Work Type"] == "Revise"


@pytest.mark.anyio
async def test_rule2_t3_revise_pass_advances_chain(httpx_mock: HTTPXMock) -> None:
    _mock_r2_reads(
        httpx_mock,
        subtask=_subtask_payload(work_type="Revise", outcome="Pass"),
        unit=_unit_r2_payload(
            stage="Intermediate", work_type="Revise", lifecycle="Active", rev_done=1, rev_target=4
        ),
    )
    _mock_no_replay(httpx_mock)
    _mock_r2_writes(httpx_mock)
    async with JiraClient() as client:
        result = await rule2_subtask_done(_done_event(), client, run_id=1, now=_R2_NOW)
    assert result == "T3"
    body = _subtask_post_body(httpx_mock)
    assert body["summary"] == "[Intermediate][Revise#3] \u2014 Two Sum"  # next index = k+1 = 3
    # Mon (Apr 20) + Gap[3] = 11 business days → 2026-05-05 (Tue)
    assert body["duedate"] == "2026-05-05"
    assert _put_body(httpx_mock)["Revision Done"] == 2


@pytest.mark.anyio
async def test_rule2_t4_auto_pauses_at_target(httpx_mock: HTTPXMock) -> None:
    _mock_r2_reads(
        httpx_mock,
        subtask=_subtask_payload(work_type="Revise", outcome="Pass"),
        unit=_unit_r2_payload(
            stage="Advanced", work_type="Revise", lifecycle="Active", rev_done=2, rev_target=3
        ),
    )
    _mock_no_replay(httpx_mock)
    _mock_r2_writes(httpx_mock, create_subtask=False)
    async with JiraClient() as client:
        result = await rule2_subtask_done(_done_event(), client, run_id=1, now=_R2_NOW)
    assert result == "T4"
    # No successor Sub-task is created on T4.
    assert not any(
        r.method == "POST" and r.url.path.endswith("/issue") for r in httpx_mock.get_requests()
    )
    put = _put_body(httpx_mock)
    assert put["Revision Done"] == 3
    assert put["Lifecycle"] == "Paused"
    assert "Paused At" in put


@pytest.mark.anyio
async def test_rule2_t12_revise_regress_resets_chain(httpx_mock: HTTPXMock) -> None:
    _mock_r2_reads(
        httpx_mock,
        subtask=_subtask_payload(work_type="Revise", outcome="Regress"),
        unit=_unit_r2_payload(
            stage="Beginner", work_type="Revise", lifecycle="Active", rev_done=2, rev_target=4
        ),
    )
    _mock_no_replay(httpx_mock)
    _mock_r2_writes(httpx_mock)
    async with JiraClient() as client:
        result = await rule2_subtask_done(_done_event(), client, run_id=1, now=_R2_NOW)
    assert result == "T12"
    body = _subtask_post_body(httpx_mock)
    assert body["summary"] == "[Beginner][Revise#1] \u2014 Two Sum"
    assert body["duedate"] == "2026-04-22"  # reset to Gap[1] = 2bd
    assert _put_body(httpx_mock)["Revision Done"] == 0


@pytest.mark.anyio
async def test_rule2_t13_test_regress_switches_worktype(httpx_mock: HTTPXMock) -> None:
    _mock_r2_reads(
        httpx_mock,
        subtask=_subtask_payload(work_type="Test", outcome="Regress"),
        unit=_unit_r2_payload(
            stage="Intermediate", work_type="Learn", lifecycle="Active", rev_done=0, rev_target=3
        ),
    )
    _mock_no_replay(httpx_mock)
    _mock_r2_writes(httpx_mock)
    async with JiraClient() as client:
        result = await rule2_subtask_done(_done_event(), client, run_id=1, now=_R2_NOW)
    assert result == "T13"
    body = _subtask_post_body(httpx_mock)
    assert body["summary"] == "[Intermediate][Revise#1] \u2014 Two Sum"
    put = _put_body(httpx_mock)
    assert put["Work Type"] == "Revise"
    assert put["Revision Done"] == 0


@pytest.mark.anyio
async def test_rule2_test_pass_is_noop(httpx_mock: HTTPXMock) -> None:
    _mock_r2_reads(
        httpx_mock,
        subtask=_subtask_payload(work_type="Test", outcome="Pass"),
        unit=_unit_r2_payload(
            stage="Beginner", work_type="Learn", lifecycle="Active", rev_done=0, rev_target=2
        ),
    )
    async with JiraClient() as client:
        result = await rule2_subtask_done(_done_event(), client, run_id=1, now=_R2_NOW)
    assert result is None
    assert not any(r.method in {"POST", "PUT"} for r in httpx_mock.get_requests())


@pytest.mark.anyio
async def test_rule2_replay_is_noop(httpx_mock: HTTPXMock) -> None:
    _mock_r2_reads(
        httpx_mock,
        subtask=_subtask_payload(work_type="Revise", outcome="Pass"),
        unit=_unit_r2_payload(
            stage="Intermediate", work_type="Revise", lifecycle="Active", rev_done=1, rev_target=4
        ),
    )
    httpx_mock.add_response(
        url=_SEARCH_URL_RE, json={"total": 1, "issues": [{"key": NEW_SUBTASK_KEY}]}
    )
    async with JiraClient() as client:
        result = await rule2_subtask_done(_done_event(), client, run_id=1, now=_R2_NOW)
    assert result is None
    assert not any(r.method in {"POST", "PUT"} for r in httpx_mock.get_requests())


@pytest.mark.parametrize(
    ("is_done", "issuetype"),
    [(False, "Sub-task"), (True, "Problem"), (True, "Epic")],
)
@pytest.mark.anyio
async def test_rule2_filters_non_subtask_done_events(
    is_done: bool, issuetype: str, httpx_mock: HTTPXMock
) -> None:
    async with JiraClient() as client:
        result = await rule2_subtask_done(
            _done_event(is_done=is_done, issuetype=issuetype), client, run_id=1, now=_R2_NOW
        )
    assert result is None
    assert httpx_mock.get_requests() == []


# ---------------------------------------------------------------------------
# Rule 4 - Stale scan (M7)
# ---------------------------------------------------------------------------

_R4_NOW = datetime.fromisoformat("2026-04-20T09:00:00+00:00")  # Monday


def _stale_candidate(
    key: str = UNIT_KEY, *, stage: str = "Intermediate", summary: str = "Binary search"
) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "Stage": {"value": stage},
            HAS_HAD_TEST_FIELD: False,
        },
    }


@pytest.mark.anyio
async def test_rule4_happy_path_creates_test_subtask(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_SEARCH_URL_RE,
        json={"total": 1, "issues": [_stale_candidate()]},
    )
    # Replay-guard lookup: no prior idem label, proceed with write.
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 0, "issues": []})
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue", method="POST", json={"key": "PROJ-500"}
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}", method="PUT", status_code=204
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{UNIT_KEY}/comment",
        method="POST",
        json={"id": "11001"},
    )
    async with JiraClient() as client:
        processed = await rule4_stale_scan(client, run_id=42, now=_R4_NOW)
    assert processed == [UNIT_KEY]
    subtask_post = next(
        r for r in httpx_mock.get_requests() if r.method == "POST" and r.url.path.endswith("/issue")
    )
    body = json.loads(subtask_post.content)["fields"]
    assert body["summary"] == "[Intermediate][Test] \u2014 Binary search"
    assert body["duedate"] == "2026-04-22"  # Mon + 2bd
    assert "test" in body["labels"]
    assert any(lbl.startswith("idem:") for lbl in body["labels"])
    put = next(r for r in httpx_mock.get_requests() if r.method == "PUT")
    assert json.loads(put.content)["fields"] == {HAS_HAD_TEST_FIELD: True}


@pytest.mark.anyio
async def test_rule4_replay_idem_label_short_circuits(httpx_mock: HTTPXMock) -> None:
    """Second scan of a Unit with an existing idem label must not re-fire T9."""
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 1, "issues": [_stale_candidate()]})
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 1, "issues": [{"key": "PROJ-SUB"}]})
    async with JiraClient() as client:
        processed = await rule4_stale_scan(client, run_id=7, now=_R4_NOW)
    assert processed == []
    assert not any(r.method in {"POST", "PUT"} for r in httpx_mock.get_requests())


@pytest.mark.anyio
async def test_rule4_empty_candidate_pool_is_noop(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 0, "issues": []})
    async with JiraClient() as client:
        processed = await rule4_stale_scan(client, run_id=42, now=_R4_NOW)
    assert processed == []
    assert not any(r.method in {"POST", "PUT"} for r in httpx_mock.get_requests())


@pytest.mark.anyio
async def test_rule4_uses_ip_stale_eligible_filter(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 0, "issues": []})
    async with JiraClient() as client:
        await rule4_stale_scan(client, run_id=1, now=_R4_NOW)
    request = next(r for r in httpx_mock.get_requests() if r.url.path.endswith("/search"))
    assert f'filter = "{STALE_ELIGIBLE_FILTER}"' == request.url.params["jql"]


@pytest.mark.anyio
async def test_rule4_skips_candidate_missing_stage(httpx_mock: HTTPXMock) -> None:
    bad = {"key": "PROJ-77", "fields": {"summary": "No stage", HAS_HAD_TEST_FIELD: False}}
    good = _stale_candidate(key="PROJ-78")
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 2, "issues": [bad, good]})
    # Replay-guard lookup for the good candidate only (bad one is skipped pre-guard).
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 0, "issues": []})
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue", method="POST", json={"key": "PROJ-501"}
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/PROJ-78", method="PUT", status_code=204
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/PROJ-78/comment",
        method="POST",
        json={"id": "11002"},
    )
    async with JiraClient() as client:
        processed = await rule4_stale_scan(client, run_id=1, now=_R4_NOW)
    assert processed == ["PROJ-78"]
