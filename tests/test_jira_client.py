"""Async test suite for runner.jira_client.JiraClient.

Covers the eight M3.2 scenarios: happy-path JSON round-trip, Basic-auth
header shape, 5xx retry, 429 retry, 401 no-retry, 404 no-retry, owned-
client close-on-aexit, and injected-client preserved-on-aexit. Uses
pytest-httpx to intercept ``httpx.AsyncClient`` transports; the
tenacity wait policy is patched to ``wait_none()`` per test so the
suite completes in milliseconds rather than the production 30s cap.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock
from tenacity import wait_none

from runner.config import get_settings
from runner.jira_client import JiraClient

BASE_URL = "https://example.atlassian.net"
ISSUE_KEY = "PROJ-1"
ISSUE_URL = f"{BASE_URL}/rest/api/3/issue/{ISSUE_KEY}"

BASE_ENV: dict[str, str] = {
    "JIRA_URL": BASE_URL,
    "JIRA_USER": "alice@example.com",
    "JIRA_TOKEN": "s3cret-token",
    "JIRA_PROJECT_KEY": "PROJ",
}

ISSUE_PAYLOAD: dict[str, Any] = {
    "key": ISSUE_KEY,
    "fields": {"summary": "example", "status": {"name": "Done"}},
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _env_and_fast_retry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Seed the JIRA_* env, reset the settings cache, and zero the retry wait."""
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    monkeypatch.setattr(JiraClient._request.retry, "wait", wait_none())  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_get_issue_returns_parsed_json(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, json=ISSUE_PAYLOAD)
    async with JiraClient() as client:
        result = await client.get_issue(ISSUE_KEY)
    assert result == ISSUE_PAYLOAD


@pytest.mark.anyio
async def test_basic_auth_header_shape(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, json=ISSUE_PAYLOAD)
    async with JiraClient() as client:
        await client.get_issue(ISSUE_KEY)
    request = httpx_mock.get_requests()[0]
    expected = "Basic " + base64.b64encode(b"alice@example.com:s3cret-token").decode("ascii")
    assert request.headers["Authorization"] == expected


@pytest.mark.anyio
async def test_retries_on_5xx_then_succeeds(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, status_code=503)
    httpx_mock.add_response(url=ISSUE_URL, status_code=503)
    httpx_mock.add_response(url=ISSUE_URL, json=ISSUE_PAYLOAD)
    async with JiraClient() as client:
        result = await client.get_issue(ISSUE_KEY)
    assert result == ISSUE_PAYLOAD
    assert len(httpx_mock.get_requests()) == 3


@pytest.mark.anyio
async def test_retries_on_429_then_succeeds(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, status_code=429)
    httpx_mock.add_response(url=ISSUE_URL, json=ISSUE_PAYLOAD)
    async with JiraClient() as client:
        result = await client.get_issue(ISSUE_KEY)
    assert result == ISSUE_PAYLOAD
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.anyio
async def test_no_retry_on_401_unauthorized(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, status_code=401)
    async with JiraClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_issue(ISSUE_KEY)
    assert exc_info.value.response.status_code == 401
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.anyio
async def test_no_retry_on_404_not_found(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, status_code=404)
    async with JiraClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_issue(ISSUE_KEY)
    assert exc_info.value.response.status_code == 404
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.anyio
async def test_owned_client_closed_on_aexit() -> None:
    client = JiraClient()
    underlying = client._client
    async with client:
        assert not underlying.is_closed
    assert underlying.is_closed


@pytest.mark.anyio
async def test_injected_client_not_closed_on_aexit() -> None:
    external = httpx.AsyncClient(base_url=BASE_URL)
    async with JiraClient(client=external):
        pass
    assert not external.is_closed
    await external.aclose()


@pytest.mark.anyio
async def test_count_issues_returns_total(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/search?jql=project+%3D+PROJ&maxResults=0",
        json={"total": 42, "issues": []},
    )
    async with JiraClient() as client:
        total = await client.count_issues("project = PROJ")
    assert total == 42


@pytest.mark.anyio
async def test_post_comment_wraps_text_in_adf(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue/{ISSUE_KEY}/comment",
        method="POST",
        json={"id": "10001"},
    )
    async with JiraClient() as client:
        await client.post_comment(ISSUE_KEY, "line1\nline2")
    request = httpx_mock.get_requests()[0]
    import json as _json

    sent = _json.loads(request.content)
    paragraph = sent["body"]["content"][0]
    assert paragraph["type"] == "paragraph"
    texts = [node.get("text") for node in paragraph["content"] if node["type"] == "text"]
    assert texts == ["line1", "line2"]
    assert any(node["type"] == "hardBreak" for node in paragraph["content"])
