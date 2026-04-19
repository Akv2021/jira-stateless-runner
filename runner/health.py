"""Dead-man's-switch state machine (docs/ExternalRunner.md §6.3-§6.4).

State layout mirrors the JSON blob persisted in GH Actions Cache under
``runner-health-state`` (§3.5). On failure, ``consecutive_failures``
increments and the classified failure kind maps through ``THRESHOLDS``
to decide whether to open a GitHub alert issue via ``gh issue create``;
the open issue URL is mirrored into the Jira System Config for
durability across cache eviction. On three consecutive successful
runs, ``maybe_close_alert`` closes the issue via ``gh issue comment``
+ ``gh issue close`` and clears the mirror.

Side-effects are isolated behind thin ``subprocess.run`` wrappers so
unit tests patch a single seam.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from runner.jira_client import IssueNotFoundError

RECOVERY_STREAK_TARGET: Final[int] = 3
"""Consecutive successful runs before ``maybe_close_alert`` closes (§6.4)."""

_NEVER_ALERTS: Final[int] = 10**9
"""Sentinel threshold for non-alerting failure kinds (e.g. ``not_found``)."""

THRESHOLDS: Final[dict[str, int]] = {
    "http_401": 1,
    "http_429": 5,
    "http_5xx": 3,
    "logic": 1,
    "not_found": _NEVER_ALERTS,
}
"""Per-kind failure thresholds (§6.3). ``http_401`` / ``logic`` are stop-the-line.

``not_found`` is a deliberate non-alerting bucket: per §6.1 a 404 means
the target issue was deleted mid-flight by the user, a legitimate event
that must never page on-call. The sentinel threshold ensures
``record_failure`` returns ``False`` even on the first occurrence.
"""

_DEFAULT_STATE_PATH: Final[Path] = Path(".runner-state/health.json")


@dataclass
class HealthState:
    """JSON-serialisable health tracker (§6.3)."""

    consecutive_failures: int = 0
    recovery_streak: int = 0
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_failure_kind: str | None = None
    open_alert_issue: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def classify(exc: BaseException) -> str:
    """Return the §6.3 failure-kind label for ``exc`` (HTTP status or ``logic``).

    ``IssueNotFoundError`` and raw ``HTTPStatusError(404)`` both route to
    ``not_found`` per §6.1 -- user-deleted issues are not alert-worthy.
    """
    if isinstance(exc, IssueNotFoundError):
        return "not_found"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "http_401"
        if code == 404:
            return "not_found"
        if code == 429:
            return "http_429"
        if 500 <= code < 600:
            return "http_5xx"
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return "http_5xx"
    return "logic"


def load_state(path: Path | None = None) -> HealthState:
    """Read ``health.json`` at ``path`` (default ``.runner-state/health.json``)."""
    target = path if path is not None else _DEFAULT_STATE_PATH
    if not target.exists():
        return HealthState()
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return HealthState()
    return HealthState(
        consecutive_failures=int(raw.get("consecutive_failures", 0)),
        recovery_streak=int(raw.get("recovery_streak", 0)),
        last_success_at=raw.get("last_success_at"),
        last_failure_at=raw.get("last_failure_at"),
        last_failure_kind=raw.get("last_failure_kind"),
        open_alert_issue=raw.get("open_alert_issue"),
        extras={k: v for k, v in raw.items() if k not in HealthState().to_dict()},
    )


def save_state(state: HealthState, path: Path | None = None) -> None:
    """Persist ``state`` to ``path`` (creating parent directories as needed)."""
    target = path if path is not None else _DEFAULT_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state.to_dict(), default=str), encoding="utf-8")


def _format_alert_body(state: HealthState, error: BaseException) -> str:
    kind = state.last_failure_kind or "unknown"
    threshold = THRESHOLDS.get(kind, 1)
    return (
        f"## Failure kind\n{kind}\n\n"
        f"## Consecutive failures\n{state.consecutive_failures}  (threshold: {threshold})\n\n"
        f"## Last success\n{state.last_success_at or 'never'}\n\n"
        f"## Last failure\n"
        f"- **When:** {state.last_failure_at}\n"
        f"- **Symptom:** {type(error).__name__}: {error}\n\n"
        "## Auto-close\nThis issue auto-closes after 3 consecutive successful runs.\n"
    )


def open_alert(state: HealthState, error: BaseException) -> str:
    """Shell out to ``gh issue create`` and return the created issue URL (§6.4).

    The URL is persisted on the state object so subsequent runs know an
    alert is live; callers should mirror it to the Jira System Config
    ``Open Alert Issue Url`` field via ``runner.watermark.write_field``
    for cache-eviction durability.
    """
    kind = state.last_failure_kind or "unknown"
    count = state.consecutive_failures
    body = _format_alert_body(state, error)
    result = subprocess.run(
        [
            "gh",
            "issue",
            "create",
            "--title",
            f"Runner System Alert: {kind} ({count} consecutive)",
            "--label",
            "system-alert,runner",
            "--body",
            body,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    issue_url = result.stdout.strip()
    state.open_alert_issue = issue_url
    return issue_url


def maybe_close_alert(state: HealthState) -> bool:
    """Close the open alert if the recovery streak has reached the target.

    Returns ``True`` if the close path fired this call, ``False``
    otherwise. ``state.recovery_streak`` is incremented on every
    successful run; this function is a no-op when no alert is open.
    """
    if state.open_alert_issue is None:
        return False
    state.recovery_streak += 1
    if state.recovery_streak < RECOVERY_STREAK_TARGET:
        return False
    url = state.open_alert_issue
    subprocess.run(
        [
            "gh",
            "issue",
            "comment",
            url,
            "--body",
            f"Auto-closing: {RECOVERY_STREAK_TARGET} consecutive successful runs "
            f"since {state.last_failure_at}.",
        ],
        check=True,
    )
    subprocess.run(["gh", "issue", "close", url], check=True)
    state.open_alert_issue = None
    state.recovery_streak = 0
    return True


def record_success(state: HealthState) -> None:
    """Apply a successful-run delta to ``state`` (§6.3)."""
    state.consecutive_failures = 0
    state.last_success_at = _utc_now_iso()


def record_failure(state: HealthState, error: BaseException) -> bool:
    """Apply a failed-run delta to ``state`` and return True if an alert should open.

    The caller is responsible for calling ``open_alert`` when this
    returns ``True`` -- keeping the subprocess out of the pure-state
    function lets tests verify the counter logic without mocking ``gh``.
    """
    kind = classify(error)
    state.consecutive_failures += 1
    state.last_failure_at = _utc_now_iso()
    state.last_failure_kind = kind
    state.recovery_streak = 0
    threshold = THRESHOLDS.get(kind, 1)
    return state.consecutive_failures >= threshold and state.open_alert_issue is None


__all__ = [
    "RECOVERY_STREAK_TARGET",
    "THRESHOLDS",
    "HealthState",
    "classify",
    "load_state",
    "maybe_close_alert",
    "open_alert",
    "record_failure",
    "record_success",
    "save_state",
]
