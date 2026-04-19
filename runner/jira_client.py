"""Async Jira REST client with tenacity-based retry resilience.

Per docs/ExternalRunner.md §2.4 and §3.2: the runner talks to Jira over
HTTPS with Basic-auth (user + API token); the client must be async so
the runner's event loop stays non-blocking, and must retry on transient
server-side failures (5xx) and rate-limit responses (429) so a hiccup
in Jira does not abort a dispatch cycle.

Design notes:

- The ``AsyncClient`` is built from ``get_settings()`` by default, but
  an injected client is accepted for test fixtures (DI).
- Retries use exponential backoff with jitter (1s initial, 30s cap,
  max 5 attempts). 4xx responses other than 429 propagate immediately
  as ``httpx.HTTPStatusError`` so the caller sees structured auth /
  permission / not-found errors without retry noise.
- The client owns its ``httpx.AsyncClient`` only when not injected; use
  ``async with JiraClient() as c: ...`` or call ``await c.aclose()``
  to release connections.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from runner.config import Settings, get_settings

_RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient HTTP/network failures worth retrying."""
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS_CODES
    return False


class JiraClient:
    """Async Jira REST v3 client bound to the process ``Settings``.

    Construct directly (``JiraClient()``) to use the cached
    ``get_settings()`` singleton, or pass an explicit ``Settings`` for
    isolation (tests, multi-tenant). Pass an ``httpx.AsyncClient`` via
    ``client=`` to share transports or mount ``respx``/``pytest-httpx``
    mocks.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings: Settings = settings if settings is not None else get_settings()
        self._owns_client: bool = client is None
        self._client: httpx.AsyncClient = client if client is not None else self._build_client()

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=str(self._settings.jira_url),
            auth=httpx.BasicAuth(
                self._settings.jira_user,
                self._settings.jira_token.get_secret_value(),
            ),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def __aenter__(self) -> JiraClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` if owned."""
        if self._owns_client:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=1, max=30),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue an HTTP request with retry on 5xx / 429 / transport errors.

        Non-retryable 4xx responses propagate as ``HTTPStatusError`` on
        the first attempt; the caller decides whether to treat them as
        hard failures (auth, not-found) or domain-level outcomes.
        """
        response = await self._client.request(method, path, params=params, json=json)
        response.raise_for_status()
        return response

    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Fetch a Jira issue by key and return its raw JSON payload.

        Baseline connectivity method: hits ``/rest/api/3/issue/{key}``
        and returns the parsed JSON dict with no field filtering. Raises
        ``httpx.HTTPStatusError`` on 404 (issue missing) or 401/403
        (auth / permission). See docs/JiraImplementation.md §2 for the
        schema of the returned payload.
        """
        response = await self._request("GET", f"/rest/api/3/issue/{issue_key}")
        result: dict[str, Any] = response.json()
        return result

    async def count_issues(self, jql: str) -> int:
        """Return the number of issues matching ``jql``.

        Uses ``/rest/api/3/search`` with ``maxResults=0`` so Jira returns
        only the ``total`` counter without any issue payload. Intended
        for existence checks such as the idempotency-label lookup in
        ``runner.idempotency.has_been_applied`` (§5.3).
        """
        response = await self._request(
            "GET",
            "/rest/api/3/search",
            params={"jql": jql, "maxResults": 0},
        )
        payload: dict[str, Any] = response.json()
        total = payload.get("total", 0)
        return int(total)

    async def search_issues(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the issue payloads matching ``jql`` (single-page; no pagination).

        Thin wrapper around ``/rest/api/3/search``. The caller supplies
        ``fields`` to restrict the returned column set; passing ``None``
        keeps Jira's default column projection. ``max_results`` is
        clamped to 50 by default — Rule 4 (§4.2) and bootstrap checks
        (§3.3) are both bounded small-batch reads; larger pulls must
        paginate explicitly via ``startAt``.
        """
        params: dict[str, Any] = {"jql": jql, "maxResults": max_results}
        if fields is not None:
            params["fields"] = ",".join(fields)
        response = await self._request("GET", "/rest/api/3/search", params=params)
        payload: dict[str, Any] = response.json()
        issues = payload.get("issues", [])
        return list(issues) if isinstance(issues, list) else []

    async def get_changelog(
        self, issue_key: str, *, start_at: int = 0, max_results: int = 100
    ) -> dict[str, Any]:
        """Fetch the per-issue changelog page for ``issue_key``.

        Hits ``/rest/api/3/issue/{key}/changelog`` per §3.4 and returns
        the raw paginated payload (``values`` + ``startAt`` + ``total``).
        Callers assemble the event stream by advancing ``startAt`` until
        the page is exhausted.
        """
        response = await self._request(
            "GET",
            f"/rest/api/3/issue/{issue_key}/changelog",
            params={"startAt": start_at, "maxResults": max_results},
        )
        payload: dict[str, Any] = response.json()
        return payload

    async def post_comment(self, issue_key: str, body_text: str) -> dict[str, Any]:
        """Post a plain-text comment on ``issue_key`` and return the Jira payload.

        Wraps ``body_text`` in the minimal Atlassian Document Format
        envelope required by the Jira REST API v3 comment endpoint, with
        newlines preserved via ``hardBreak`` nodes so the §5.2 multi-line
        audit template renders correctly in the Jira UI.
        """
        content: list[dict[str, Any]] = []
        for index, line in enumerate(body_text.split("\n")):
            if index > 0:
                content.append({"type": "hardBreak"})
            if line:
                content.append({"type": "text", "text": line})
        adf_body: dict[str, Any] = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": content}],
            }
        }
        response = await self._request(
            "POST", f"/rest/api/3/issue/{issue_key}/comment", json=adf_body
        )
        result: dict[str, Any] = response.json()
        return result

    async def create_subtask(
        self,
        *,
        parent_key: str,
        summary: str,
        labels: list[str],
        story_points: int | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a ``Sub-task`` under ``parent_key`` and return the Jira payload.

        ``labels`` must include the ``idem:<hex>`` tag per §5.3 so the
        created Subtask is replay-identifiable. ``extra_fields`` is
        merged last and can override any field the runner sets by
        default; intended for per-rule additions (Work Type, Due Date)
        from ``runner.rules``.
        """
        from runner.config import get_settings as _get_settings

        project_key = _get_settings().jira_project_key
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "parent": {"key": parent_key},
            "summary": summary,
            "issuetype": {"name": "Sub-task"},
            "labels": list(labels),
        }
        if story_points is not None:
            fields["Story Points"] = story_points
        if extra_fields:
            fields.update(extra_fields)
        response = await self._request("POST", "/rest/api/3/issue", json={"fields": fields})
        result: dict[str, Any] = response.json()
        return result

    async def update_issue(self, issue_key: str, fields: dict[str, Any]) -> None:
        """Edit ``issue_key`` with the supplied ``fields`` payload.

        Wraps ``PUT /rest/api/3/issue/{key}``; returns ``None`` on the
        Jira 204 success. Raises ``httpx.HTTPStatusError`` on 4xx. Field
        names must match the site's Jira schema; mapping of display
        names (``Revision Target``) to ``customfield_XXXXX`` IDs is the
        caller's responsibility until the M8 bootstrap lands.
        """
        await self._request("PUT", f"/rest/api/3/issue/{issue_key}", json={"fields": fields})
