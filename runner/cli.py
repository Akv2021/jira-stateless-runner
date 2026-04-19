"""CLI orchestration for the three scheduled workflows.

- ``poll``   -> bootstrap check + watermark read + Rule 1/2 dispatch + watermark advance (§4.1).
- ``stale``  -> Rule 4 / T9 stale scan (§4.2).
- ``health`` -> independent watchdog against ``Last Successful Poll At`` (§6.5).

Every entrypoint is wrapped by ``_with_health_tracking`` -- success runs
reset the consecutive-failure counter and may auto-close an open alert;
failures increment the counter, classify by kind, and open a GH issue
once the kind-specific threshold trips. The wrapper mirrors the open
alert URL into the Jira System Config for cache-eviction durability.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from runner import __version__, health, watermark
from runner.config import get_settings
from runner.ingestor import ingest_issue_changelog
from runner.jira_client import JiraClient
from runner.logging_ext import get_logger
from runner.models import ChangelogEvent
from runner.rules import rule1_unit_created, rule2_subtask_done, rule4_stale_scan

_LOG = get_logger("runner.cli")

STALE_POLL_THRESHOLD = timedelta(minutes=30)
"""Watchdog threshold (§6.5) -- one missed cron plus jitter."""


def _jql_updated_since(project_key: str, since_iso: str | None) -> str:
    """Build the poll-window JQL: project-scoped, system-config excluded.

    When ``since_iso`` is present, narrows to ``updated >=`` that
    timestamp; the 1-minute cursor rewind (handled by the caller) plus
    changelog ``since_id`` filter tolerates clock skew without
    duplicating side-effects.
    """
    base = f'project = "{project_key}" AND labels != "ztmos-system"'
    if since_iso:
        # Jira JQL requires "yyyy-MM-dd HH:mm" (no T, no timezone).
        jql_ts = since_iso.replace("T", " ")[:16]
        return f'{base} AND updated >= "{jql_ts}" ORDER BY updated ASC'
    return f"{base} ORDER BY updated ASC"


async def _fetch_new_events(
    client: JiraClient, project_key: str, since_id: int, since_iso: str | None
) -> tuple[list[ChangelogEvent], int]:
    """Pull changelogs for issues updated after ``since_iso``.

    Returns the classified events with ``id > since_id`` plus the
    observed max changelog ID. Per-issue changelog fetching is bounded
    to one page (100 entries) -- issues with deeper history than that
    within a single poll window are expected to be rare; extension to
    multi-page walks ships with the first live-run iteration.
    """
    jql = _jql_updated_since(project_key, since_iso)
    issues = await client.search_issues(
        jql, fields=["issuetype", "created", "summary"], max_results=50
    )
    events: list[ChangelogEvent] = []
    max_id = since_id
    for issue in issues:
        key = issue.get("key")
        if not isinstance(key, str):
            continue
        page = await client.get_changelog(key)
        for event in ingest_issue_changelog(issue, page, since_id=since_id):
            events.append(event)
            if event.id > max_id:
                max_id = event.id
    events.sort(key=lambda e: e.id)
    return events, max_id


async def _poll() -> None:
    settings = get_settings()
    async with JiraClient() as client:
        await watermark.check_bootstrap(client)
        state = await watermark.read(client, settings.jira_project_key)
        events, max_id = await _fetch_new_events(
            client,
            settings.jira_project_key,
            state.last_processed_changelog_id,
            state.last_successful_poll_at,
        )
        run_id = int(datetime.now(tz=UTC).timestamp())
        for event in events:
            if event.is_new_issue:
                await rule1_unit_created(event, client, run_id=run_id)
            elif event.is_status_change_to_done:
                await rule2_subtask_done(event, client, run_id=run_id)
        await watermark.write(
            client,
            state,
            last_processed_changelog_id=max_id,
            runner_version=__version__,
        )


async def _stale() -> None:
    settings = get_settings()
    async with JiraClient() as client:
        await watermark.check_bootstrap(client)
        state = await watermark.read(client, settings.jira_project_key)
        run_id = int(datetime.now(tz=UTC).timestamp())
        await rule4_stale_scan(client, run_id=run_id)
        await watermark.write_stale_scan_timestamp(client, state)


async def _health() -> None:
    settings = get_settings()
    async with JiraClient() as client:
        state = await watermark.read(client, settings.jira_project_key)
    stamp = state.last_successful_poll_at
    if stamp is None:
        raise RuntimeError("No Last Successful Poll At -- poll-dispatch has never succeeded")
    last = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    age = datetime.now(tz=UTC) - last
    if age > STALE_POLL_THRESHOLD:
        raise RuntimeError(f"poll-dispatch stale: last success {last.isoformat()} ({age} ago)")


async def _mirror_alert_url(url: str | None) -> None:
    """Mirror the open-alert URL into the Jira System Config (§6.4).

    Durability hedge against GH Actions Cache eviction -- the state
    machine uses the Jira field as the canonical "is an alert open?"
    signal on the next run. Failures are logged but never re-raised;
    mirroring is best-effort and must not mask the primary failure.
    """
    try:
        settings = get_settings()
        async with JiraClient() as client:
            wstate = await watermark.read(client, settings.jira_project_key)
            await watermark.write_field(client, wstate, watermark.WATERMARK_FIELD_OPEN_ALERT, url)
    except Exception:
        _LOG.exception("alert_mirror_failed")


async def _with_health_tracking(run: Callable[[], Awaitable[None]]) -> None:
    hstate = health.load_state()
    try:
        await run()
    except Exception as exc:
        should_open = health.record_failure(hstate, exc)
        if should_open:
            try:
                url = health.open_alert(hstate, exc)
                _LOG.error("alert_opened", extra={"alert_url": url})
                await _mirror_alert_url(url)
            except Exception:
                _LOG.exception("alert_open_failed")
        health.save_state(hstate)
        raise
    else:
        health.record_success(hstate)
        try:
            closed = health.maybe_close_alert(hstate)
            if closed:
                await _mirror_alert_url(None)
        except Exception:
            _LOG.exception("alert_close_failed")
        health.save_state(hstate)


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] not in {"poll", "stale", "health"}:
        print("usage: python -m runner {poll,stale,health}", file=sys.stderr)
        return 2
    mapping: dict[str, Callable[[], Awaitable[None]]] = {
        "poll": _poll,
        "stale": _stale,
        "health": _health,
    }
    try:
        asyncio.run(_with_health_tracking(mapping[args[0]]))
    except Exception as exc:
        _LOG.exception("runner_failed", extra={"kind": health.classify(exc)})
        return 1
    return 0
