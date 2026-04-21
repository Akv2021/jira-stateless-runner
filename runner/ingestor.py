"""Changelog event ingestor for the M8 polling loop.

Per docs/ExternalRunner.md §3.4 and §4.1: Jira's `/search` endpoint does
not surface changelog IDs directly, so the runner polls the per-issue
`/changelog` endpoint and classifies each raw history entry into a
``ChangelogEvent`` with the ``issuetype`` / ``is_new_issue`` /
``is_status_change_to_done`` metadata populated — the two rule handlers
(§4.1) filter on those booleans without a second Jira fetch.

Classification is intentionally small:

- ``is_new_issue`` -- the issue's ``created`` timestamp equals the entry's
  ``created`` timestamp. Note that Jira Cloud Free does not emit a real
  changelog entry on issue creation, so this branch fires only on
  deployments that do (Jira Cloud Premium workflow post-functions, or
  a Jira Automation rule that writes a marker field at creation). For
  free-tier deployments ``runner.cli._maybe_synthesise_creation``
  mints the missing event outside this classifier.
- ``is_status_change_to_done`` -- the entry carries a ``status`` field
  delta whose ``to``-string is the terminal status name ``"Done"``.
- Everything else falls through as a plain event with both booleans
  ``False`` — Rules 1 and 2 silently skip those.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from runner.models import ChangelogEvent, ChangelogItem

DONE_STATUS_NAME: Final[str] = "Done"
"""Jira status name emitted on Sub-task close (§4.1 Rule 2 filter)."""


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_item(raw: dict[str, Any]) -> ChangelogItem | None:
    field = raw.get("field")
    if not isinstance(field, str) or not field:
        return None
    return ChangelogItem(
        field=field,
        fromString=raw.get("fromString") if isinstance(raw.get("fromString"), str) else None,
        toString=raw.get("toString") if isinstance(raw.get("toString"), str) else None,
    )


def classify_event(raw_entry: dict[str, Any], issue_meta: dict[str, Any]) -> ChangelogEvent | None:
    """Build a ``ChangelogEvent`` from one raw history row plus issue metadata.

    ``raw_entry`` is a single element of Jira's changelog ``values``
    array; ``issue_meta`` is the enclosing issue payload (or a minimal
    projection thereof) carrying ``key``, ``fields.issuetype``, and
    ``fields.created``. Returns ``None`` when the entry is malformed
    (missing ``id`` / ``created`` / ``issue_meta.key``), letting the
    polling loop skip it without aborting the batch.
    """
    raw_id = raw_entry.get("id")
    try:
        event_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        event_id = None
    if event_id is None:
        return None
    created = _parse_iso(raw_entry.get("created"))
    if created is None:
        return None
    issue_key = issue_meta.get("key")
    if not isinstance(issue_key, str):
        return None

    fields = issue_meta.get("fields") or {}
    issuetype_raw = fields.get("issuetype")
    if isinstance(issuetype_raw, dict):
        name = issuetype_raw.get("name")
        issuetype = name if isinstance(name, str) else None
    elif isinstance(issuetype_raw, str):
        issuetype = issuetype_raw
    else:
        issuetype = None

    items = tuple(
        item
        for item in (_to_item(i) for i in raw_entry.get("items") or [] if isinstance(i, dict))
        if item is not None
    )

    issue_created = _parse_iso(fields.get("created"))
    is_new_issue = issue_created is not None and issue_created == created

    is_status_change_to_done = any(
        item.field.lower() == "status" and item.to_value == DONE_STATUS_NAME for item in items
    )

    author = raw_entry.get("author")
    author_account_id: str | None = None
    if isinstance(author, dict):
        candidate = author.get("accountId")
        if isinstance(candidate, str):
            author_account_id = candidate

    return ChangelogEvent(
        id=event_id,
        issue_key=issue_key,
        created=created,
        author_account_id=author_account_id,
        items=items,
        issuetype=issuetype,
        is_new_issue=is_new_issue,
        is_status_change_to_done=is_status_change_to_done,
    )


def ingest_issue_changelog(
    issue_meta: dict[str, Any],
    changelog_page: dict[str, Any],
    *,
    since_id: int = 0,
) -> list[ChangelogEvent]:
    """Return the classified events for one issue whose ID exceeds ``since_id``.

    Events are sorted in ascending ``id`` order so the caller can track
    ``max_changelog_id`` with a simple running max.
    """
    values = changelog_page.get("values") or []
    events: list[ChangelogEvent] = []
    for raw in values:
        if not isinstance(raw, dict):
            continue
        event = classify_event(raw, issue_meta)
        if event is None or event.id <= since_id:
            continue
        events.append(event)
    events.sort(key=lambda e: e.id)
    return events


__all__ = [
    "DONE_STATUS_NAME",
    "classify_event",
    "ingest_issue_changelog",
]
