"""Dead-man's-switch tests for runner.health (M9, §6.3-§6.4).

`subprocess.run` is patched so `open_alert` / `maybe_close_alert` never
actually shell out; tests assert both the counter arithmetic and the
shape of the `gh` command the module would issue.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from runner.health import (
    RECOVERY_STREAK_TARGET,
    THRESHOLDS,
    HealthState,
    classify,
    load_state,
    maybe_close_alert,
    open_alert,
    record_failure,
    record_success,
    save_state,
)
from runner.jira_client import IssueNotFoundError


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.atlassian.net/rest/api/3/issue/PROJ-1")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_http_error(401), "http_401"),
        (_http_error(403), "http_401"),
        (_http_error(404), "not_found"),
        (IssueNotFoundError("/rest/api/3/issue/PROJ-1"), "not_found"),
        (_http_error(429), "http_429"),
        (_http_error(503), "http_5xx"),
        (httpx.ConnectError("down"), "http_5xx"),
        (ValueError("oops"), "logic"),
    ],
)
def test_classify_maps_exception_to_kind(exc: BaseException, expected: str) -> None:
    assert classify(exc) == expected


def test_record_failure_not_found_never_opens_alert() -> None:
    """§6.1: user-deleted issues are not alert-worthy regardless of frequency."""
    state = HealthState()
    for _ in range(20):
        should_open = record_failure(state, IssueNotFoundError("/rest/api/3/issue/PROJ-9"))
        assert should_open is False
    assert state.last_failure_kind == "not_found"
    assert state.consecutive_failures == 20
    assert state.consecutive_failures < THRESHOLDS["not_found"]


def test_record_success_resets_counters() -> None:
    state = HealthState(consecutive_failures=3, last_failure_kind="http_5xx")
    record_success(state)
    assert state.consecutive_failures == 0
    assert state.last_success_at is not None


def test_record_failure_crosses_threshold_for_401() -> None:
    state = HealthState()
    should_open = record_failure(state, _http_error(401))
    assert state.consecutive_failures == 1
    assert state.last_failure_kind == "http_401"
    assert should_open is True


def test_record_failure_under_threshold_does_not_open() -> None:
    state = HealthState()
    should_open = record_failure(state, _http_error(429))
    assert should_open is False
    assert state.consecutive_failures < THRESHOLDS["http_429"]


def test_record_failure_respects_existing_open_alert() -> None:
    state = HealthState(open_alert_issue="https://gh.test/issue/1")
    should_open = record_failure(state, _http_error(401))
    assert should_open is False  # already open
    assert state.consecutive_failures == 1


def test_open_alert_invokes_gh_and_mirrors_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout="https://gh.test/issue/42\n", stderr=""
        )

    monkeypatch.setattr("runner.health.subprocess.run", fake_run)
    state = HealthState(
        consecutive_failures=1,
        last_failure_kind="http_401",
        last_failure_at="2026-04-20T09:00:00+00:00",
    )
    url = open_alert(state, _http_error(401))
    assert url == "https://gh.test/issue/42"
    assert state.open_alert_issue == url
    assert calls[0][:3] == ["gh", "issue", "create"]
    assert "--label" in calls[0]
    assert "system-alert,runner" in calls[0]


def test_maybe_close_alert_closes_after_recovery_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("runner.health.subprocess.run", fake_run)
    state = HealthState(
        open_alert_issue="https://gh.test/issue/99",
        last_failure_at="2026-04-20T09:00:00+00:00",
    )
    for _ in range(RECOVERY_STREAK_TARGET - 1):
        assert maybe_close_alert(state) is False
    closed = maybe_close_alert(state)
    assert closed is True
    assert state.open_alert_issue is None
    assert state.recovery_streak == 0
    commands = [c[:3] for c in calls]
    assert ["gh", "issue", "comment"] in commands
    assert ["gh", "issue", "close"] in commands


def test_maybe_close_alert_noop_when_no_alert() -> None:
    state = HealthState()
    assert maybe_close_alert(state) is False


def test_load_and_save_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    state = HealthState(
        consecutive_failures=2,
        recovery_streak=1,
        last_failure_kind="http_429",
        open_alert_issue="https://gh.test/issue/5",
    )
    save_state(state, path)
    restored = load_state(path)
    assert restored.consecutive_failures == 2
    assert restored.open_alert_issue == "https://gh.test/issue/5"
    assert restored.last_failure_kind == "http_429"


def test_load_state_missing_file_returns_defaults(tmp_path: Path) -> None:
    state = load_state(tmp_path / "missing.json")
    assert state == HealthState()
