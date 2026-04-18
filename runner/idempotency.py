"""Idempotency-key computation and label-based replay protection.

Per docs/ExternalRunner.md §5.3 and docs/ImplementationRoadmap.md M4:
each ``(unit_key, event_id, transition_id)`` triple hashes to a 12-hex
digest; the digest is affixed to the Subtask created by a transition
as a Jira label of the form ``idem:<hex>``. A replay is detected via a
JQL existence check for that label under the parent Unit, guaranteeing
exactly-once side-effects under polling retry.

Label-convention decision: the ``idem_`` prefix shown in the §5.3 code
block is retained for the **display form** in audit comments (``key:
idem_<hex>``) but stripped from the Jira **label** itself
(``idem:<hex>``) so the label matches both the §5.3 inline example and
the roadmap M4 deliverable spec (``sha256(...)[:12]``). The two forms
share the same 12-hex payload, so a round-trip is unambiguous.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Protocol

from runner.models import TransitionID


class HasIssueKey(Protocol):
    """Minimal structural contract for replay-safety lookups.

    The idempotency helpers only read ``.key`` off their unit argument,
    so any object exposing a string ``key`` attribute — the full
    ``runner.models.Unit`` model or the lightweight stub constructed in
    ``runner.rules`` — is accepted.
    """

    @property
    def key(self) -> str: ...


IDEM_LABEL_NAMESPACE = "idem"
"""Jira-label prefix for the idempotency key (separator ``:``)."""

IDEM_DISPLAY_PREFIX = "idem_"
"""Human-readable prefix used by the §5.2 audit-comment ``key:`` token."""

_HEX_LEN = 12

CountFn = Callable[[str], Awaitable[int]]
"""Async callable ``(jql) -> total``; satisfied by ``JiraClient.count_issues``.

Decouples idempotency from the client class so tests can inject a
trivial async stub without constructing an ``httpx.AsyncClient``.
"""


def compute_key(unit_key: str, event_id: str, transition_id: TransitionID) -> str:
    """Return the 12-hex-character idempotency key for one transition.

    Deterministic: the same ``(unit_key, event_id, transition_id)``
    always produces the same key. Collision surface is 48 bits — ample
    for the ~10^3 events/year the runner services per §5.3.
    """
    raw = f"{unit_key}|{event_id}|{transition_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_HEX_LEN]


def label_for(key: str) -> str:
    """Return the Jira label carrying ``key`` (e.g. ``idem:8c4d2a1f9bb7``)."""
    return f"{IDEM_LABEL_NAMESPACE}:{key}"


def display_for(key: str) -> str:
    """Return the audit-comment display form of ``key`` (e.g. ``idem_8c4d2a1f9bb7``)."""
    return f"{IDEM_DISPLAY_PREFIX}{key}"


def replay_jql(unit: HasIssueKey, key: str) -> str:
    """Return the JQL that locates Subtasks of ``unit`` carrying ``idem:<key>``."""
    return f'parent = "{unit.key}" AND labels = "{label_for(key)}"'


async def has_been_applied(unit: HasIssueKey, key: str, count: CountFn) -> bool:
    """Return True if a Subtask under ``unit`` already carries ``idem:<key>``.

    The ``count`` callable is expected to execute ``replay_jql(unit,
    key)`` against Jira and return the total matching issue count;
    ``JiraClient.count_issues`` satisfies this contract. Any non-zero
    result signals a prior application and short-circuits side-effects
    in the caller.
    """
    total = await count(replay_jql(unit, key))
    return total > 0


__all__ = [
    "IDEM_DISPLAY_PREFIX",
    "IDEM_LABEL_NAMESPACE",
    "CountFn",
    "HasIssueKey",
    "compute_key",
    "display_for",
    "has_been_applied",
    "label_for",
    "replay_jql",
]
