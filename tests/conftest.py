"""Shared pytest fixtures for the runner test suite.

The autouse ``_mock_field_map`` fixture queues a response for
``GET /rest/api/3/field`` whenever a test opts into ``pytest_httpx``,
so tests that exercise ``JiraClient.update_issue`` / ``create_subtask``
(both of which now translate display-name keys to ``customfield_XXXXX``
IDs via ``get_field_map`` — see docs/ExternalRunner.md §3.2 M8) can
succeed without every test re-registering the same response.

``CUSTOM_FIELD_IDS`` mirrors the ``name -> id`` pairs returned by
``FIELD_MAP_RESPONSE`` and is consumed by assertions that need to
reference the post-translation payload shape (e.g.
``body[CUSTOM_FIELD_IDS["Revision Target"]]``). Add an entry here when
a new display-name-keyed field enters the runner.
"""

from __future__ import annotations

import re
from typing import Any, Final

import pytest
from pytest_httpx import HTTPXMock

CUSTOM_FIELD_IDS: Final[dict[str, str]] = {
    # Watermark fields (runner/watermark.py)
    "Last Processed Changelog Id": "customfield_10144",
    "Last Successful Poll At": "customfield_10145",
    "Last Stale Scan At": "customfield_10146",
    "Runner Version": "customfield_10147",
    "Open Alert Issue Url": "customfield_10148",
    # Unit / Sub-task fields (runner/rules.py)
    "Stage": "customfield_10100",
    "Difficulty": "customfield_10101",
    "Work Type": "customfield_10102",
    "Lifecycle": "customfield_10103",
    "Revision Done": "customfield_10104",
    "Revision Target": "customfield_10105",
    "Paused At": "customfield_10106",
    "Last Transitioned At": "customfield_10107",
    "Last Subtask Completed At": "customfield_10108",
    "Outcome": "customfield_10109",
    "Has Had Test": "customfield_10110",
    "Story Points": "customfield_10111",
}
"""``display_name -> customfield_XXXXX`` for every custom field the runner writes.

Must stay in sync with ``FIELD_MAP_RESPONSE``.
"""

_SYSTEM_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    # (id, display-name) pairs matching the real Jira response shape.
    ("issuetype", "Issue Type"),
    ("created", "Created"),
    ("summary", "Summary"),
    ("labels", "Labels"),
    ("parent", "Parent"),
    ("status", "Status"),
)

FIELD_MAP_RESPONSE: Final[list[dict[str, Any]]] = [
    {"id": field_id, "name": name, "custom": True} for name, field_id in CUSTOM_FIELD_IDS.items()
] + [{"id": fid, "name": dname, "custom": False} for fid, dname in _SYSTEM_FIELDS]
"""``GET /rest/api/3/field`` response body consumed by the autouse mock.

Includes both custom fields (consumed by the runner via display-name
keys) and a representative slice of system fields (returned by Jira
with ``custom: false`` and a display name that differs from the
canonical id). ``get_field_map`` filters to custom fields only so
system fields keep their canonical lowercase ids on reads.
"""

_FIELD_MAP_URL_RE = re.compile(r".*/rest/api/3/field$")


@pytest.fixture(autouse=True)
def _mock_field_map(request: pytest.FixtureRequest) -> None:
    """Queue a reusable ``GET /rest/api/3/field`` response when httpx is mocked.

    Only activates if the test already requested the ``httpx_mock``
    fixture (directly or transitively) — so pure-logic tests such as
    ``test_state_machine.py`` remain untouched. The response is marked
    ``is_optional=True`` / ``is_reusable=True`` so tests that never
    trigger a write (and therefore never call ``get_field_map``) do
    not fail with "unused response", and tests that instantiate
    multiple clients within one test share a single cached mock.
    """
    if "httpx_mock" not in request.fixturenames:
        return
    httpx_mock: HTTPXMock = request.getfixturevalue("httpx_mock")
    httpx_mock.add_response(
        url=_FIELD_MAP_URL_RE,
        method="GET",
        json=FIELD_MAP_RESPONSE,
        is_optional=True,
        is_reusable=True,
    )
