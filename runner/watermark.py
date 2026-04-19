"""Polling watermark I/O against the Jira System Config issue.

Per docs/ExternalRunner.md §3.2-§3.4:

- ``read()``  -> ``WatermarkState`` from the System Config issue.
- ``write()`` -> advances ``Last Processed Changelog Id`` + ``Last Successful Poll At``.
- ``write_field()`` -> single-field write, used by ``health.py`` to mirror
  the open-alert URL into the System Config for cache-eviction durability.
- ``check_bootstrap()`` -> §3.3 self-check that every user-facing saved
  filter excludes ``labels = "runner-system"``. Raises
  ``BootstrapIncompleteError`` listing any un-amended filters so the
  runner fails fast at first invocation rather than silently polluting
  reports at read-time.

The System Config issue is located by the ``labels = "runner-system"``
marker under the configured project; exactly one must exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from runner.jira_client import JiraClient

SYSTEM_LABEL: Final[str] = "runner-system"
"""Label marking the System Config issue (§3.2)."""

WATERMARK_FIELD_ID: Final[str] = "Last Processed Changelog Id"
WATERMARK_FIELD_LAST_POLL: Final[str] = "Last Successful Poll At"
WATERMARK_FIELD_LAST_STALE: Final[str] = "Last Stale Scan At"
WATERMARK_FIELD_RUNNER_VERSION: Final[str] = "Runner Version"
WATERMARK_FIELD_OPEN_ALERT: Final[str] = "Open Alert Issue Url"

MANDATORY_FILTERS: Final[tuple[str, ...]] = (
    "IP-Working-Set",
    "IP-Stale",
    "IP-Paused-FIFO",
    "IP-Archive",
    "IP-Velocity-LT",
    "IP-Stale-Eligible",
)
"""Saved filters that MUST exclude ``labels = "runner-system"`` (§3.3).

``IP-Now`` is excluded because its ``issuetype = Sub-task`` clause
already precludes the System Config issue.
"""


class BootstrapIncompleteError(RuntimeError):
    """Raised by ``check_bootstrap`` when any mandatory filter is un-amended.

    The offending filter names are carried on ``.unamended`` so callers
    can surface an actionable error message without re-parsing the
    exception string.
    """

    def __init__(self, unamended: list[str]) -> None:
        self.unamended = list(unamended)
        names = ", ".join(self.unamended)
        super().__init__(
            f"Bootstrap incomplete: the following saved filters still match "
            f'the System Config issue (missing `AND labels != "{SYSTEM_LABEL}"`): {names}'
        )


@dataclass(frozen=True)
class WatermarkState:
    """Snapshot of the System Config issue's watermark fields (§3.2)."""

    issue_key: str
    last_processed_changelog_id: int
    last_successful_poll_at: str | None
    last_stale_scan_at: str | None
    runner_version: str | None
    open_alert_issue_url: str | None


def _read_scalar(payload: dict[str, Any], name: str) -> Any:
    fields = payload.get("fields") or {}
    return fields.get(name)


async def find_system_config_issue(client: JiraClient, project_key: str) -> str:
    """Return the Jira issue key of the project's System Config issue.

    Raises ``BootstrapIncompleteError`` if zero or more-than-one match --
    both cases indicate Phase 1 provisioning has not completed cleanly
    (§3.2 requires exactly one per project).
    """
    jql = f'project = "{project_key}" AND labels = "{SYSTEM_LABEL}"'
    issues = await client.search_issues(jql, fields=["summary"], max_results=2)
    if len(issues) != 1:
        raise BootstrapIncompleteError([f"SystemConfig:{project_key} count={len(issues)}"])
    key = issues[0].get("key")
    if not isinstance(key, str):
        raise BootstrapIncompleteError([f"SystemConfig:{project_key} missing key"])
    return key


async def read(client: JiraClient, project_key: str) -> WatermarkState:
    """Return the persisted watermark tuple for ``project_key`` (§3.4)."""
    key = await find_system_config_issue(client, project_key)
    payload = await client.get_issue(key)
    raw_id = _read_scalar(payload, WATERMARK_FIELD_ID)
    changelog_id = int(raw_id) if isinstance(raw_id, int | float) else 0
    return WatermarkState(
        issue_key=key,
        last_processed_changelog_id=changelog_id,
        last_successful_poll_at=_read_scalar(payload, WATERMARK_FIELD_LAST_POLL),
        last_stale_scan_at=_read_scalar(payload, WATERMARK_FIELD_LAST_STALE),
        runner_version=_read_scalar(payload, WATERMARK_FIELD_RUNNER_VERSION),
        open_alert_issue_url=_read_scalar(payload, WATERMARK_FIELD_OPEN_ALERT),
    )


async def write(
    client: JiraClient,
    state: WatermarkState,
    *,
    last_processed_changelog_id: int,
    runner_version: str,
    now: datetime | None = None,
) -> None:
    """Advance the watermark atomically at the end of a clean poll run (§3.4)."""
    stamp = (now if now is not None else datetime.now(tz=UTC)).isoformat()
    await client.update_issue(
        state.issue_key,
        {
            WATERMARK_FIELD_ID: last_processed_changelog_id,
            WATERMARK_FIELD_LAST_POLL: stamp,
            WATERMARK_FIELD_RUNNER_VERSION: runner_version,
        },
    )


async def write_field(
    client: JiraClient, state: WatermarkState, field_name: str, value: Any
) -> None:
    """Write a single field on the System Config issue (health mirror-path)."""
    await client.update_issue(state.issue_key, {field_name: value})


async def write_stale_scan_timestamp(
    client: JiraClient, state: WatermarkState, *, now: datetime | None = None
) -> None:
    """Stamp ``Last Stale Scan At`` at the end of a Rule 4 run (§3.2)."""
    stamp = (now if now is not None else datetime.now(tz=UTC)).isoformat()
    await client.update_issue(state.issue_key, {WATERMARK_FIELD_LAST_STALE: stamp})


async def check_bootstrap(client: JiraClient, filters: tuple[str, ...] = MANDATORY_FILTERS) -> None:
    """Run the §3.3 self-check: every filter must exclude the System Config.

    For each name in ``filters``, issues the JQL
    ``filter = "<name>" AND labels = "runner-system"`` against Jira. Any
    non-zero hit means the filter was never amended, and the runner
    aborts with ``BootstrapIncompleteError`` listing every offender in
    one pass so the operator can remediate them all before retrying.
    """
    unamended: list[str] = []
    for name in filters:
        jql = f'filter = "{name}" AND labels = "{SYSTEM_LABEL}"'
        if await client.count_issues(jql) > 0:
            unamended.append(name)
    if unamended:
        raise BootstrapIncompleteError(unamended)


__all__ = [
    "MANDATORY_FILTERS",
    "SYSTEM_LABEL",
    "WATERMARK_FIELD_ID",
    "WATERMARK_FIELD_LAST_POLL",
    "WATERMARK_FIELD_LAST_STALE",
    "WATERMARK_FIELD_OPEN_ALERT",
    "WATERMARK_FIELD_RUNNER_VERSION",
    "BootstrapIncompleteError",
    "WatermarkState",
    "check_bootstrap",
    "find_system_config_issue",
    "read",
    "write",
    "write_field",
    "write_stale_scan_timestamp",
]
