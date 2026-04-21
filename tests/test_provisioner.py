"""Tests for ``scripts.provision_jira`` saved-filter reconciliation.

Focused on the ``--update-filters`` flag: the default mode stays purely
additive (add missing, leave existing), while the flag rewrites any
filter whose ``jql`` or ``description`` has drifted from ``FILTERS``.
Uses ``pytest-httpx`` to stub ``/rest/api/3/filter/...`` endpoints and
monkeypatches ``FILTERS`` to a single spec per test for isolation.
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock
from tenacity import wait_none

from scripts import provision_jira
from scripts.provision_jira import FilterSpec, Provisioner

BASE_URL = "https://example.atlassian.net"
SEARCH_URL = (
    f"{BASE_URL}/rest/api/3/filter/search"
    "?filterName=IP-Working-Set&maxResults=10&expand=jql%2Cdescription"
)
POST_URL = f"{BASE_URL}/rest/api/3/filter"

SPEC = FilterSpec(
    name="IP-Working-Set",
    description="Current Working Set — Active Units",
    jql='issuetype != Sub-task AND "Lifecycle" = "Active" '
    'AND (labels IS EMPTY OR labels != "runner-system") '
    'ORDER BY "Last Worked At" DESC',
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero the tenacity wait so retry-bearing requests don't sleep."""
    monkeypatch.setattr(Provisioner._request.retry, "wait", wait_none())  # type: ignore[attr-defined]


@pytest.fixture
def _single_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse ``FILTERS`` to the single ``SPEC`` for test isolation."""
    monkeypatch.setattr(provision_jira, "FILTERS", (SPEC,))


async def _run(update: bool) -> Provisioner:
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        prov = Provisioner(client, account_id="acct-1", update_filters=update)
        await prov.ensure_filters()
    return prov


@pytest.mark.anyio
@pytest.mark.usefixtures("_single_filter")
async def test_ensure_filters_creates_when_missing(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=SEARCH_URL, json={"values": []})
    httpx_mock.add_response(url=POST_URL, method="POST", json={"id": "42"})
    prov = await _run(update=False)
    assert prov.summary.filters_created == {"IP-Working-Set": 42}
    assert prov.summary.filters_existed == {}
    assert prov.summary.filters_updated == {}


@pytest.mark.anyio
@pytest.mark.usefixtures("_single_filter")
async def test_ensure_filters_no_update_flag_leaves_drifted_untouched(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=SEARCH_URL,
        json={
            "values": [
                {
                    "id": "7",
                    "name": "IP-Working-Set",
                    "jql": 'labels != "runner-system"',  # drifted from SPEC
                    "description": "stale",
                }
            ]
        },
    )
    prov = await _run(update=False)
    assert prov.summary.filters_existed == {"IP-Working-Set": 7}
    assert prov.summary.filters_updated == {}
    # No PUT should have been issued.
    assert not [r for r in httpx_mock.get_requests() if r.method == "PUT"]


@pytest.mark.anyio
@pytest.mark.usefixtures("_single_filter")
async def test_ensure_filters_update_flag_rewrites_drifted(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=SEARCH_URL,
        json={
            "values": [
                {
                    "id": "7",
                    "name": "IP-Working-Set",
                    "jql": 'labels != "runner-system"',
                    "description": "stale",
                }
            ]
        },
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/filter/7",
        method="PUT",
        json={"id": "7"},
    )
    prov = await _run(update=True)
    assert prov.summary.filters_updated == {"IP-Working-Set": 7}
    assert prov.summary.filters_existed == {}
    put = next(r for r in httpx_mock.get_requests() if r.method == "PUT")
    body = put.read().decode()
    assert "IS EMPTY" in body
    assert "Current Working Set" in body


@pytest.mark.anyio
@pytest.mark.usefixtures("_single_filter")
async def test_ensure_filters_update_flag_noop_when_in_sync(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url=SEARCH_URL,
        json={
            "values": [
                {
                    "id": "7",
                    "name": SPEC.name,
                    "jql": SPEC.jql,
                    "description": SPEC.description,
                }
            ]
        },
    )
    prov = await _run(update=True)
    assert prov.summary.filters_existed == {"IP-Working-Set": 7}
    assert prov.summary.filters_updated == {}
    assert not [r for r in httpx_mock.get_requests() if r.method == "PUT"]
