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
from typing import Any

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

    The system-config exclusion uses ``(labels IS EMPTY OR labels !=
    "runner-system")`` rather than a bare ``labels != "runner-system"``.
    In JQL semantics a bare ``!=`` on ``labels`` matches only issues
    that have at least one label, so fresh Units created without any
    labels (the common Jira Cloud default) are silently dropped from
    the poll window. Verified empirically on 2026-04-21 against
    COREPREP-3/-4/-5, which were invisible to the bare form despite
    being updated strictly after the watermark.

    When ``since_iso`` is present, narrows to ``updated >=`` that
    timestamp; the 1-minute cursor rewind (handled by the caller) plus
    changelog ``since_id`` filter tolerates clock skew without
    duplicating side-effects.
    """
    base = f'project = "{project_key}" AND (labels IS EMPTY OR labels != "runner-system")'
    if since_iso:
        # Jira JQL requires "yyyy-MM-dd HH:mm" (no T, no timezone).
        jql_ts = since_iso.replace("T", " ")[:16]
        return f'{base} AND updated >= "{jql_ts}" ORDER BY updated ASC'
    return f"{base} ORDER BY updated ASC"


def _parse_iso(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _maybe_synthesise_creation(
    issue: dict[str, Any],
    since_iso: str | None,
    real_events: list[ChangelogEvent],
) -> ChangelogEvent | None:
    """Return a synthetic ``is_new_issue`` event for a freshly-created Unit.

    Jira Cloud Free does not emit a changelog entry on issue creation,
    even when custom fields are set in the initial ``POST /issue`` body
    (verified empirically on 2026-04-20 against COREPREP-2). That breaks
    the ``issue.created == entry.created`` contract the ingestor relies
    on to flag ``is_new_issue``, so Rule 1 never fires autonomously
    without this bridge. When the issue was created strictly after the
    last successful poll and the real changelog carries no creation
    event, we mint one here so the normal dispatch path can drive T1.

    Cold-start (``since_iso is None``) is intentionally skipped: the
    first poll after bootstrap would otherwise synthesise events for
    every historical issue in the project. Replay safety is provided
    by Rule 1's ``idem:<hex>`` label, which stays stable because the
    synthetic ``id`` is a constant and the idempotency key is hashed
    from ``(issue_key, "0", "T1")``.

    The synthetic ``id`` is held at ``0`` so the real-event watermark
    (``max_id`` in ``_fetch_new_events``) keeps advancing only on
    observed Jira changelog rows.
    """
    if since_iso is None:
        return None
    fields = issue.get("fields") or {}
    created = _parse_iso(fields.get("created"))
    since = _parse_iso(since_iso)
    if created is None or since is None or created <= since:
        return None
    if any(ev.is_new_issue for ev in real_events):
        return None
    key = issue.get("key")
    if not isinstance(key, str):
        return None
    issuetype_raw = fields.get("issuetype")
    itype: str | None = None
    if isinstance(issuetype_raw, dict):
        name = issuetype_raw.get("name")
        if isinstance(name, str):
            itype = name
    return ChangelogEvent(
        id=0,
        issue_key=key,
        created=created,
        issuetype=itype,
        is_new_issue=True,
    )


async def _fetch_new_events(
    client: JiraClient, project_key: str, since_id: int, since_iso: str | None
) -> tuple[list[ChangelogEvent], int]:
    """Pull changelogs for issues updated after ``since_iso``.

    Returns the classified events with ``id > since_id`` plus the
    observed max changelog ID. Per-issue changelog fetching walks
    every page via ``JiraClient.iter_changelog_pages`` so an issue
    whose full history exceeds the 100-entry default does not silently
    truncate; the walker stops when Jira reports ``isLast == True``.

    Also synthesises a creation event per
    ``_maybe_synthesise_creation`` to bridge the Jira Cloud Free
    no-create-changelog gap; synthetic events are appended per issue
    but held out of ``max_id`` so they do not poison the watermark.
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
        pages = await client.iter_changelog_pages(key)
        issue_events: list[ChangelogEvent] = []
        for page in pages:
            for event in ingest_issue_changelog(issue, page, since_id=since_id):
                issue_events.append(event)
                if event.id > max_id:
                    max_id = event.id
        events.extend(issue_events)
        synthetic = _maybe_synthesise_creation(issue, since_iso, issue_events)
        if synthetic is not None:
            events.append(synthetic)
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
    # Cold-start no-op: the System Config has never been written to
    # (neither `Last Successful Poll At` nor `Runner Version` is set).
    # There is no poll history to watchdog yet, so the healthcheck
    # succeeds silently -- alerting here would trip on every M11 D.1
    # dry-run before the first successful poll completes.
    if stamp is None and state.runner_version is None:
        _LOG.info("healthcheck_cold_start", extra={"unit": state.issue_key})
        return
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
