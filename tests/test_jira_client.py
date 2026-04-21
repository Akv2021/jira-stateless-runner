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
from runner.jira_client import IssueNotFoundError, JiraClient

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


def _issue_requests(httpx_mock: HTTPXMock) -> list[httpx.Request]:
    """Return only the requests against ``ISSUE_URL`` (drops field-map noise)."""
    return [r for r in httpx_mock.get_requests() if str(r.url).startswith(ISSUE_URL)]


@pytest.mark.anyio
async def test_retries_on_5xx_then_succeeds(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, status_code=503)
    httpx_mock.add_response(url=ISSUE_URL, status_code=503)
    httpx_mock.add_response(url=ISSUE_URL, json=ISSUE_PAYLOAD)
    async with JiraClient() as client:
        result = await client.get_issue(ISSUE_KEY)
    assert result == ISSUE_PAYLOAD
    assert len(_issue_requests(httpx_mock)) == 3


@pytest.mark.anyio
async def test_retries_on_429_then_succeeds(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, status_code=429)
    httpx_mock.add_response(url=ISSUE_URL, json=ISSUE_PAYLOAD)
    async with JiraClient() as client:
        result = await client.get_issue(ISSUE_KEY)
    assert result == ISSUE_PAYLOAD
    assert len(_issue_requests(httpx_mock)) == 2


@pytest.mark.anyio
async def test_no_retry_on_401_unauthorized(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, status_code=401)
    async with JiraClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_issue(ISSUE_KEY)
    assert exc_info.value.response.status_code == 401
    assert len(_issue_requests(httpx_mock)) == 1


@pytest.mark.anyio
async def test_no_retry_on_404_not_found(httpx_mock: HTTPXMock) -> None:
    # 404 is translated to IssueNotFoundError per ExternalRunner.md §6.1 so
    # rule handlers can swallow the deleted-mid-flight case without hitting
    # the retry policy or the generic HTTPStatusError alerting path.
    httpx_mock.add_response(url=ISSUE_URL, status_code=404)
    async with JiraClient() as client:
        with pytest.raises(IssueNotFoundError) as exc_info:
            await client.get_issue(ISSUE_KEY)
    assert ISSUE_KEY in exc_info.value.path
    assert len(_issue_requests(httpx_mock)) == 1


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
        url=f"{BASE_URL}/rest/api/3/search/approximate-count",
        method="POST",
        json={"count": 42},
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


# ---------------------------------------------------------------------------
# M8 field-discovery bootstrap — get_field_map + display-name translation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_field_map_builds_name_to_id_dict(httpx_mock: HTTPXMock) -> None:
    async with JiraClient() as client:
        mapping = await client.get_field_map()
    # The autouse field-map fixture in conftest.py supplies the mock
    # response; the resolver turns it into a plain {name: id} dict.
    assert mapping["Revision Target"] == "customfield_10105"
    assert mapping["Last Processed Changelog Id"] == "customfield_10144"


@pytest.mark.anyio
async def test_get_field_map_excludes_system_fields(httpx_mock: HTTPXMock) -> None:
    """System fields (custom: false) must not enter the translation map.

    Otherwise ``_translate_payload_fields`` rewrites ``issuetype`` →
    ``"Issue Type"`` and silently breaks ``runner.ingestor`` and
    ``runner.cli._maybe_synthesise_creation``, which address system
    fields by their canonical lowercase id.
    """
    async with JiraClient() as client:
        mapping = await client.get_field_map()
    assert "Issue Type" not in mapping
    assert "Created" not in mapping
    assert "Summary" not in mapping
    assert "Labels" not in mapping


@pytest.mark.anyio
async def test_get_field_map_is_cached_per_client(httpx_mock: HTTPXMock) -> None:
    async with JiraClient() as client:
        await client.get_field_map()
        await client.get_field_map()
        await client.get_field_map()
    field_calls = [r for r in httpx_mock.get_requests() if r.url.path.endswith("/rest/api/3/field")]
    assert len(field_calls) == 1


@pytest.mark.anyio
async def test_update_issue_translates_display_names_to_ids(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ISSUE_URL, method="PUT", status_code=204)
    async with JiraClient() as client:
        await client.update_issue(ISSUE_KEY, {"Revision Target": 3, "summary": "new"})
    put = next(r for r in httpx_mock.get_requests() if r.method == "PUT")
    import json as _json

    body = _json.loads(put.content)["fields"]
    # Custom field rewritten to its ID; system field passes through.
    assert body == {"customfield_10105": 3, "summary": "new"}


@pytest.mark.anyio
async def test_create_subtask_translates_extra_fields(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue", method="POST", json={"key": "PROJ-43"}
    )
    async with JiraClient() as client:
        await client.create_subtask(
            parent_key="PROJ-42",
            summary="child",
            labels=["idem:abc"],
            story_points=2,
            extra_fields={"Work Type": "Revise", "duedate": "2026-04-22"},
        )
    post = next(
        r for r in httpx_mock.get_requests() if r.method == "POST" and r.url.path.endswith("/issue")
    )
    import json as _json

    body = _json.loads(post.content)["fields"]
    assert body["customfield_10102"] == "Revise"  # Work Type -> custom
    assert body["customfield_10111"] == 2  # Story Points -> custom
    assert body["duedate"] == "2026-04-22"  # system field, unchanged
    assert body["summary"] == "child"
    assert body["parent"] == {"key": "PROJ-42"}


@pytest.mark.anyio
async def test_create_subtask_retries_without_story_points_on_screen_error(
    httpx_mock: HTTPXMock,
) -> None:
    """Tenant field-config scheme that hides Story Points must not wedge Rule 1."""
    import json as _json

    # First POST: Jira rejects the SP field.
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue",
        method="POST",
        status_code=400,
        json={
            "errors": {
                "customfield_10111": (
                    "Field 'customfield_10111' cannot be set. "
                    "It is not on the appropriate screen, or unknown."
                )
            }
        },
    )
    # Second POST: retry without SP succeeds.
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue", method="POST", json={"key": "PROJ-99"}
    )
    async with JiraClient() as client:
        result = await client.create_subtask(
            parent_key="PROJ-42",
            summary="child",
            labels=["idem:abc"],
            story_points=2,
        )
    assert result == {"key": "PROJ-99"}
    posts = [
        r for r in httpx_mock.get_requests() if r.method == "POST" and r.url.path.endswith("/issue")
    ]
    assert len(posts) == 2
    first_body = _json.loads(posts[0].content)["fields"]
    assert first_body.get("customfield_10111") == 2
    second_body = _json.loads(posts[1].content)["fields"]
    assert "customfield_10111" not in second_body
    # All other fields preserved.
    assert second_body["parent"] == {"key": "PROJ-42"}
    assert second_body["summary"] == "child"
    assert second_body["labels"] == ["idem:abc"]


@pytest.mark.anyio
async def test_create_subtask_propagates_unrelated_400(httpx_mock: HTTPXMock) -> None:
    """400 errors unrelated to Story Points must still surface as failures."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/issue",
        method="POST",
        status_code=400,
        json={"errors": {"summary": "Summary is required."}},
    )
    async with JiraClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.create_subtask(
                parent_key="PROJ-42",
                summary="",
                labels=["idem:abc"],
                story_points=2,
            )


@pytest.mark.anyio
async def test_get_issue_rewrites_custom_field_ids_to_display_names(
    httpx_mock: HTTPXMock,
) -> None:
    """Real Jira returns ``customfield_XXXXX`` keys; the client rewrites them.

    Regression for the M8 read-side translation (docs/ExternalRunner.md
    §3.2): ``runner.rules`` addresses fields by display name, so the
    client must inverse-map every known ID on the way out. System fields
    (absent from the field-map) pass through unchanged.
    """
    httpx_mock.add_response(
        url=ISSUE_URL,
        json={
            "key": ISSUE_KEY,
            "fields": {
                "summary": "system-field stays",  # system field, passes through
                "customfield_10100": {"value": "Intermediate"},  # Stage
                "customfield_10104": 2,  # Revision Done
                "customfield_99999": "unknown",  # unmapped ID also passes through
            },
        },
    )
    async with JiraClient() as client:
        payload = await client.get_issue(ISSUE_KEY)
    fields = payload["fields"]
    assert fields["Stage"] == {"value": "Intermediate"}
    assert fields["Revision Done"] == 2
    assert fields["summary"] == "system-field stays"
    assert fields["customfield_99999"] == "unknown"


@pytest.mark.anyio
async def test_search_issues_rewrites_custom_field_ids_to_display_names(
    httpx_mock: HTTPXMock,
) -> None:
    """``search_issues`` applies the same inverse map as ``get_issue`` per-issue."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/rest/api/3/search/jql",
        method="POST",
        json={
            "issues": [
                {
                    "key": "PROJ-7",
                    "fields": {
                        "summary": "pass-through",
                        "customfield_10102": {"value": "Learn"},  # Work Type
                        "customfield_10110": True,  # Has Had Test
                    },
                }
            ]
        },
    )
    async with JiraClient() as client:
        issues = await client.search_issues("project = PROJ")
    assert len(issues) == 1
    fields = issues[0]["fields"]
    assert fields["Work Type"] == {"value": "Learn"}
    assert fields["Has Had Test"] is True
    assert fields["summary"] == "pass-through"


@pytest.mark.anyio
async def test_iter_changelog_pages_walks_until_is_last(httpx_mock: HTTPXMock) -> None:
    """The paginator advances ``startAt`` until Jira reports ``isLast=True``.

    Covers the M12 multi-page changelog walk: an issue with more than
    ``max_results`` history entries must surface every page to the
    ingestor, not just the first 100 rows.
    """
    changelog_url = f"{BASE_URL}/rest/api/3/issue/{ISSUE_KEY}/changelog"
    httpx_mock.add_response(
        url=f"{changelog_url}?startAt=0&maxResults=2",
        method="GET",
        json={
            "startAt": 0,
            "maxResults": 2,
            "isLast": False,
            "values": [{"id": "1"}, {"id": "2"}],
        },
    )
    httpx_mock.add_response(
        url=f"{changelog_url}?startAt=2&maxResults=2",
        method="GET",
        json={
            "startAt": 2,
            "maxResults": 2,
            "isLast": True,
            "values": [{"id": "3"}],
        },
    )
    async with JiraClient() as client:
        pages = await client.iter_changelog_pages(ISSUE_KEY, max_results=2)
    assert [entry["id"] for page in pages for entry in page["values"]] == ["1", "2", "3"]


@pytest.mark.anyio
async def test_iter_changelog_pages_stops_on_short_page_without_is_last(
    httpx_mock: HTTPXMock,
) -> None:
    """Older responses may omit ``isLast``; the walker stops on a short page."""
    changelog_url = f"{BASE_URL}/rest/api/3/issue/{ISSUE_KEY}/changelog"
    httpx_mock.add_response(
        url=f"{changelog_url}?startAt=0&maxResults=2",
        method="GET",
        json={"startAt": 0, "maxResults": 2, "values": [{"id": "1"}]},
    )
    async with JiraClient() as client:
        pages = await client.iter_changelog_pages(ISSUE_KEY, max_results=2)
    assert len(pages) == 1


@pytest.mark.anyio
async def test_iter_changelog_pages_raises_on_page_cap(httpx_mock: HTTPXMock) -> None:
    """Runaway pagination trips a RuntimeError classified as ``logic`` by health."""
    changelog_url = f"{BASE_URL}/rest/api/3/issue/{ISSUE_KEY}/changelog"
    for start in (0, 1, 2):
        httpx_mock.add_response(
            url=f"{changelog_url}?startAt={start}&maxResults=1",
            method="GET",
            json={
                "startAt": start,
                "maxResults": 1,
                "isLast": False,
                "values": [{"id": str(start + 1)}],
            },
        )
    async with JiraClient() as client:
        with pytest.raises(RuntimeError, match="page_cap"):
            await client.iter_changelog_pages(ISSUE_KEY, max_results=1, page_cap=3)
