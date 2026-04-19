"""CLI orchestration smoke tests for runner.cli (M10)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from runner import cli, health


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolate_health_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect the default health-state path into tmp so tests do not clash."""
    monkeypatch.setattr(health, "_DEFAULT_STATE_PATH", tmp_path / "health.json")


def test_main_rejects_unknown_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["nope"]) == 2
    assert "usage:" in capsys.readouterr().err


def test_main_rejects_no_args(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.anyio
async def test_with_health_tracking_records_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _ok() -> None:
        calls.append("ran")

    # Skip the alert-mirror path since it needs live Jira; success path
    # never calls it because no alert was open.
    await cli._with_health_tracking(_ok)
    state = health.load_state()
    assert calls == ["ran"]
    assert state.consecutive_failures == 0
    assert state.last_success_at is not None


@pytest.mark.anyio
async def test_with_health_tracking_opens_alert_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[BaseException] = []
    mirrored: list[str | None] = []

    def fake_open_alert(state: health.HealthState, exc: BaseException) -> str:
        opened.append(exc)
        state.open_alert_issue = "https://gh.test/issue/1"
        return "https://gh.test/issue/1"

    async def fake_mirror(url: str | None) -> None:
        mirrored.append(url)

    monkeypatch.setattr("runner.health.open_alert", fake_open_alert)
    monkeypatch.setattr(cli, "_mirror_alert_url", fake_mirror)

    async def _boom() -> None:
        request = httpx.Request("GET", "https://x.example/y")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("auth", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await cli._with_health_tracking(_boom)
    assert len(opened) == 1
    assert mirrored == ["https://gh.test/issue/1"]
    state = health.load_state()
    assert state.consecutive_failures == 1
    assert state.last_failure_kind == "http_401"
    assert state.open_alert_issue == "https://gh.test/issue/1"


@pytest.mark.anyio
async def test_with_health_tracking_closes_alert_after_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirrored: list[str | None] = []

    async def fake_mirror(url: str | None) -> None:
        mirrored.append(url)

    def fake_close(_state: health.HealthState) -> bool:
        _state.recovery_streak += 1
        if _state.recovery_streak >= 3:
            _state.open_alert_issue = None
            _state.recovery_streak = 0
            return True
        return False

    monkeypatch.setattr(cli, "_mirror_alert_url", fake_mirror)
    monkeypatch.setattr("runner.health.maybe_close_alert", fake_close)

    # Seed an open alert on disk.
    seed = health.HealthState(open_alert_issue="https://gh.test/issue/9")
    health.save_state(seed)

    async def _ok() -> None:
        return None

    for _ in range(2):
        await cli._with_health_tracking(_ok)
    assert mirrored == []
    await cli._with_health_tracking(_ok)
    assert mirrored == [None]


def test_jql_updated_since_includes_clause() -> None:
    jql = cli._jql_updated_since("PROJ", "2026-04-20T09:00:00+00:00")
    assert "PROJ" in jql
    assert 'labels != "ztmos-system"' in jql
    assert 'updated >= "2026-04-20 09:00"' in jql


def test_jql_updated_since_omits_clause_when_no_timestamp() -> None:
    jql = cli._jql_updated_since("PROJ", None)
    assert "updated >=" not in jql


@pytest.mark.anyio
async def test_with_health_tracking_below_threshold_suppresses_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[BaseException] = []

    def fake_open(_state: health.HealthState, exc: BaseException) -> str:
        opened.append(exc)
        return "never"

    monkeypatch.setattr("runner.health.open_alert", fake_open)

    async def _boom() -> None:
        request = httpx.Request("GET", "https://x.example/y")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await cli._with_health_tracking(_boom)
    assert opened == []  # 1 < threshold(5) for http_429
