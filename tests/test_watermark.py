"""Integration tests for runner.watermark + runner.ingestor (M8)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from tenacity import wait_none

from runner import watermark
from runner.config import get_settings
from runner.ingestor import classify_event, ingest_issue_changelog
from runner.jira_client import JiraClient
from runner.watermark import (
    MANDATORY_FILTERS,
    BootstrapIncompleteError,
    WatermarkState,
)

BASE_URL = "https://example.atlassian.net"
_SEARCH_URL_RE = re.compile(r"https://example\.atlassian\.net/rest/api/3/search.*")
SYS_KEY = "PROJ-1"
SYS_ISSUE_URL = f"{BASE_URL}/rest/api/3/issue/{SYS_KEY}"
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


def _system_payload(
    *,
    changelog_id: int | None = 42,
    last_poll: str | None = "2026-04-18T09:00:00+00:00",
    version: str | None = "0.1.1",
    alert_url: str | None = None,
) -> dict[str, Any]:
    return {
        "key": SYS_KEY,
        "fields": {
            "Last Processed Changelog Id": changelog_id,
            "Last Successful Poll At": last_poll,
            "Last Stale Scan At": None,
            "Runner Version": version,
            "Open Alert Issue Url": alert_url,
        },
    }


@pytest.mark.anyio
async def test_read_watermark_returns_state(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 1, "issues": [{"key": SYS_KEY}]})
    httpx_mock.add_response(url=SYS_ISSUE_URL, json=_system_payload())
    async with JiraClient() as client:
        state = await watermark.read(client, "PROJ")
    assert state.issue_key == SYS_KEY
    assert state.last_processed_changelog_id == 42
    assert state.runner_version == "0.1.1"


@pytest.mark.anyio
async def test_read_watermark_defaults_missing_id_to_zero(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 1, "issues": [{"key": SYS_KEY}]})
    httpx_mock.add_response(url=SYS_ISSUE_URL, json=_system_payload(changelog_id=None))
    async with JiraClient() as client:
        state = await watermark.read(client, "PROJ")
    assert state.last_processed_changelog_id == 0


@pytest.mark.anyio
async def test_find_system_config_raises_when_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 0, "issues": []})
    async with JiraClient() as client:
        with pytest.raises(BootstrapIncompleteError):
            await watermark.find_system_config_issue(client, "PROJ")


@pytest.mark.anyio
async def test_write_watermark_issues_put_with_all_fields(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=SYS_ISSUE_URL, method="PUT", status_code=204)
    state = WatermarkState(
        issue_key=SYS_KEY,
        last_processed_changelog_id=10,
        last_successful_poll_at=None,
        last_stale_scan_at=None,
        runner_version=None,
        open_alert_issue_url=None,
    )
    now = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    async with JiraClient() as client:
        await watermark.write(
            client, state, last_processed_changelog_id=99, runner_version="0.1.1", now=now
        )
    body = json.loads(httpx_mock.get_requests()[0].content)["fields"]
    assert body["Last Processed Changelog Id"] == 99
    assert body["Runner Version"] == "0.1.1"
    assert body["Last Successful Poll At"].startswith("2026-04-20T09:00:00")


@pytest.mark.anyio
async def test_check_bootstrap_passes_when_filters_clean(httpx_mock: HTTPXMock) -> None:
    for _ in MANDATORY_FILTERS:
        httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 0, "issues": []})
    async with JiraClient() as client:
        await watermark.check_bootstrap(client)


@pytest.mark.anyio
async def test_check_bootstrap_raises_when_filter_unamended(httpx_mock: HTTPXMock) -> None:
    for _ in MANDATORY_FILTERS[:-1]:
        httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 0, "issues": []})
    httpx_mock.add_response(url=_SEARCH_URL_RE, json={"total": 1, "issues": [{"key": SYS_KEY}]})
    async with JiraClient() as client:
        with pytest.raises(BootstrapIncompleteError) as exc:
            await watermark.check_bootstrap(client)
    assert exc.value.unamended == [MANDATORY_FILTERS[-1]]


# ---- Ingestor -------------------------------------------------------------


def _issue_meta(
    *, created: str = "2026-04-18T09:00:00.000+0000", itype: str = "Sub-task"
) -> dict[str, Any]:
    return {"key": "PROJ-42", "fields": {"issuetype": {"name": itype}, "created": created}}


def _history(*, eid: int, created: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": str(eid), "created": created, "items": items}


def test_ingestor_classifies_new_issue_creation() -> None:
    ts = "2026-04-18T09:00:00.000+0000"
    event = classify_event(_history(eid=1, created=ts, items=[]), _issue_meta(created=ts))
    assert event is not None
    assert event.is_new_issue is True


def test_ingestor_classifies_done_transition() -> None:
    item = {"field": "status", "fromString": "In Progress", "toString": "Done"}
    event = classify_event(
        _history(eid=7, created="2026-04-18T10:00:00.000+0000", items=[item]),
        _issue_meta(),
    )
    assert event is not None
    assert event.is_status_change_to_done is True


def test_ingest_issue_changelog_filters_by_since_id_and_sorts() -> None:
    issue = _issue_meta()
    page = {
        "values": [
            _history(eid=3, created="2026-04-18T11:00:00.000+0000", items=[]),
            _history(eid=1, created="2026-04-18T09:00:00.000+0000", items=[]),
            _history(eid=2, created="2026-04-18T10:00:00.000+0000", items=[]),
        ]
    }
    events = ingest_issue_changelog(issue, page, since_id=1)
    assert [e.id for e in events] == [2, 3]
