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
from runner.logging_ext import get_logger

_RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_LOG = get_logger(__name__)


def _is_story_points_screen_error(
    exc: httpx.HTTPStatusError, story_points: int | None, story_points_field_id: str | None
) -> bool:
    """Return True when the 400 response is Jira's Story Points screen gate.

    Tenants whose Sub-task field configuration or screen scheme does
    not expose the ``Story Points`` custom field respond with
    ``400 {"errors": {"customfield_XXXXX": "Field 'customfield_XXXXX'
    cannot be set. It is not on the appropriate screen, or unknown."}}``
    where ``customfield_XXXXX`` is the resolved Story Points id. The
    caller uses this signal to retry without the Story Points clause
    rather than fail the whole dispatch.
    """
    if story_points is None or story_points_field_id is None or exc.response.status_code != 400:
        return False
    try:
        errors = exc.response.json().get("errors") or {}
    except ValueError:
        return False
    msg = errors.get(story_points_field_id)
    return isinstance(msg, str) and "cannot be set" in msg


class IssueNotFoundError(Exception):
    """Raised when Jira returns 404 for a read/write on a specific issue.

    Per docs/ExternalRunner.md §6.1, 404 is a legitimate user-initiated
    deletion mid-flight and must neither retry nor advance the failure
    counter. Callers in ``runner.rules`` catch this, log WARN, and skip
    the single affected event so the rest of the poll batch proceeds.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Jira 404 Not Found: {path}")


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
        self._field_map: dict[str, str] | None = None

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

        404 responses are translated into ``IssueNotFoundError`` (a
        non-retryable, non-alerting exception per §6.1) instead of the
        generic ``HTTPStatusError``; the health-state classifier routes
        it to the ``not_found`` bucket and rule handlers catch it to skip
        the single affected event without aborting the poll batch.
        Other non-retryable 4xx responses propagate as ``HTTPStatusError``
        on the first attempt.
        """
        response = await self._client.request(method, path, params=params, json=json)
        if response.status_code == 404:
            raise IssueNotFoundError(path)
        response.raise_for_status()
        return response

    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Fetch a Jira issue by key and return its raw JSON payload.

        Baseline connectivity method: hits ``/rest/api/3/issue/{key}``
        and returns the parsed JSON dict. Raises ``IssueNotFoundError``
        on 404 (issue deleted mid-flight, §6.1) and ``httpx.HTTPStatusError``
        on 401/403 (auth / permission). The returned ``fields`` dict is
        rewritten so custom-field IDs (``customfield_XXXXX``) map back to
        their display names (M8 read-side translation) -- callers read
        by human-readable name (``"Stage"``, ``"Revision Done"``) per
        docs/JiraImplementation.md §2.
        """
        response = await self._request("GET", f"/rest/api/3/issue/{issue_key}")
        result: dict[str, Any] = response.json()
        return await self._translate_payload_fields(result)

    async def count_issues(self, jql: str) -> int:
        """Return the approximate number of issues matching ``jql``.

        Uses ``POST /rest/api/3/search/approximate-count`` (the GA
        successor to the removed ``/rest/api/3/search`` total field per
        Atlassian CHANGE-2046). The response shape is ``{"count": N}``.
        Intended for existence checks such as the idempotency-label
        lookup in ``runner.idempotency.has_been_applied`` (§5.3).
        """
        response = await self._request(
            "POST",
            "/rest/api/3/search/approximate-count",
            json={"jql": jql},
        )
        payload: dict[str, Any] = response.json()
        count = payload.get("count", 0)
        return int(count)

    async def get_field_map(self) -> dict[str, str]:
        """Return ``{display_name: field_id}`` for every **custom** field.

        Populates lazily on first call via ``GET /rest/api/3/field`` and
        caches the result on the instance for the lifetime of the
        client. Enables display-name writes (``"Revision Target"``) to
        be translated to Jira-internal IDs (``customfield_10144``) in
        ``update_issue`` / ``create_subtask`` — the M8 field-discovery
        bootstrap per docs/ExternalRunner.md §3.2.

        System fields (``issuetype``, ``created``, ``summary``,
        ``labels``, ``parent``, …) are intentionally excluded: Jira
        returns them with ``custom: false`` and a display name that
        differs from the canonical id (``"Issue Type"`` vs
        ``issuetype``). Including them would cause
        ``_translate_payload_fields`` to rewrite ``issuetype`` to
        ``"Issue Type"`` on reads, silently breaking every consumer
        that addresses system fields by their canonical lowercase key
        (``runner.ingestor``, ``runner.rules._summary`` /
        ``_parent_key``, ``runner.cli._maybe_synthesise_creation``).
        """
        if self._field_map is None:
            response = await self._request("GET", "/rest/api/3/field")
            fields = response.json()
            mapping: dict[str, str] = {}
            if isinstance(fields, list):
                for entry in fields:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("custom") is not True:
                        continue
                    name = entry.get("name")
                    field_id = entry.get("id")
                    if isinstance(name, str) and isinstance(field_id, str):
                        mapping[name] = field_id
            self._field_map = mapping
        return self._field_map

    async def _translate_field_keys(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Resolve any display-name keys in ``fields`` to their Jira field IDs.

        Keys absent from the field map pass through unchanged — this
        keeps system fields keyed by their canonical name (``summary``,
        ``labels``, ``parent``, ``duedate``) working alongside custom
        fields resolved to ``customfield_XXXXX``.
        """
        field_map = await self.get_field_map()
        return {field_map.get(k, k): v for k, v in fields.items()}

    async def _translate_payload_fields(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Rewrite ``payload["fields"]`` keys from Jira IDs to display names.

        The inverse of ``_translate_field_keys``. Real Jira
        ``GET /rest/api/3/issue`` returns custom fields keyed by
        ``customfield_XXXXX`` IDs; callers (``runner.rules``,
        ``runner.watermark``) address fields by their human-readable
        name, so the client rewrites the dict in place before returning.
        System fields (``summary``, ``labels``, ``parent``, ``duedate``)
        are absent from the inverse map and pass through unchanged.
        """
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            return payload
        field_map = await self.get_field_map()
        inverse: dict[str, str] = {v: k for k, v in field_map.items()}
        payload["fields"] = {inverse.get(k, k): v for k, v in fields.items()}
        return payload

    async def search_issues(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the issue payloads matching ``jql`` (single-page; no pagination).

        Uses ``POST /rest/api/3/search/jql`` (the GA replacement for the
        removed ``/rest/api/3/search`` per Atlassian CHANGE-2046). The
        caller supplies ``fields`` to restrict the returned column set;
        passing ``None`` keeps Jira's default column projection.
        ``max_results`` is clamped to 50 by default — Rule 4 (§4.2) and
        bootstrap checks (§3.3) are both bounded small-batch reads;
        larger pulls must paginate explicitly via ``nextPageToken``.
        Each returned issue payload has its ``fields`` dict rewritten to
        display-name keys (M8 read-side translation, mirrors ``get_issue``).
        """
        body: dict[str, Any] = {"jql": jql, "maxResults": max_results}
        if fields is not None:
            body["fields"] = fields
        response = await self._request("POST", "/rest/api/3/search/jql", json=body)
        payload: dict[str, Any] = response.json()
        issues = payload.get("issues", [])
        if not isinstance(issues, list):
            return []
        return [await self._translate_payload_fields(issue) for issue in issues]

    async def get_changelog(
        self, issue_key: str, *, start_at: int = 0, max_results: int = 100
    ) -> dict[str, Any]:
        """Fetch a single changelog page for ``issue_key``.

        Hits ``/rest/api/3/issue/{key}/changelog`` per §3.4 and returns
        the raw paginated payload (``values`` + ``startAt`` + ``total``
        + ``isLast``). Callers that need the complete history must use
        ``iter_changelog_pages`` (or advance ``startAt`` manually) until
        the payload reports ``isLast == True``.
        """
        response = await self._request(
            "GET",
            f"/rest/api/3/issue/{issue_key}/changelog",
            params={"startAt": start_at, "maxResults": max_results},
        )
        payload: dict[str, Any] = response.json()
        return payload

    async def iter_changelog_pages(
        self,
        issue_key: str,
        *,
        max_results: int = 100,
        page_cap: int = 20,
    ) -> list[dict[str, Any]]:
        """Return every changelog page for ``issue_key`` in ascending order.

        Walks ``/rest/api/3/issue/{key}/changelog`` by incrementing
        ``startAt`` until the payload reports ``isLast == True`` (Jira
        v3 contract) or the page returns fewer than ``max_results``
        entries -- the latter covers older deployments that omit the
        ``isLast`` flag. ``page_cap`` bounds the walk at 2 000 entries
        by default so a pathological issue cannot starve a poll cycle;
        hitting the cap raises ``RuntimeError`` so the outer health
        classifier can treat it as a logic failure (§6.1).
        """
        pages: list[dict[str, Any]] = []
        start_at = 0
        for _ in range(page_cap):
            page = await self.get_changelog(issue_key, start_at=start_at, max_results=max_results)
            pages.append(page)
            values = page.get("values") or []
            if not isinstance(values, list) or not values:
                return pages
            is_last = page.get("isLast")
            if isinstance(is_last, bool) and is_last:
                return pages
            if len(values) < max_results:
                return pages
            start_at += len(values)
        raise RuntimeError(f"changelog pagination exceeded page_cap={page_cap} for {issue_key}")

    async def list_comments(self, issue_key: str) -> list[dict[str, Any]]:
        """Return all comments on ``issue_key`` as a list of Jira payloads.

        Used by the §6.6 partial-success recovery path in ``runner.audit``
        to detect whether an audit comment carrying a given idempotency
        key has already been posted -- enabling replay-safe re-emission
        when a Subtask + field write succeeded but the comment POST
        failed on a prior run.
        """
        response = await self._request("GET", f"/rest/api/3/issue/{issue_key}/comment")
        payload: dict[str, Any] = response.json()
        comments = payload.get("comments", [])
        return list(comments) if isinstance(comments, list) else []

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

        ``story_points`` is best-effort: if Jira rejects the initial
        POST with a ``"Story Points" cannot be set`` error (tenant
        screens / field-config scheme do not expose the field on
        Sub-task), the Sub-task is re-created without the Story
        Points clause and a WARNING is logged. Rule 1 / 2 still
        advance; the velocity analytics documented in
        `LivingRequirements.md §6 FR11` degrade gracefully rather
        than wedge the entire poll.
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
        try:
            translated = await self._translate_field_keys(fields)
            response = await self._request("POST", "/rest/api/3/issue", json={"fields": translated})
        except httpx.HTTPStatusError as exc:
            sp_field_id = (await self.get_field_map()).get("Story Points")
            if not _is_story_points_screen_error(exc, story_points, sp_field_id):
                raise
            _LOG.warning(
                "story_points_unsettable_fallback",
                extra={"parent_key": parent_key, "story_points": story_points},
            )
            fields.pop("Story Points", None)
            translated = await self._translate_field_keys(fields)
            response = await self._request("POST", "/rest/api/3/issue", json={"fields": translated})
        result: dict[str, Any] = response.json()
        return result

    async def update_issue(self, issue_key: str, fields: dict[str, Any]) -> None:
        """Edit ``issue_key`` with the supplied ``fields`` payload.

        Wraps ``PUT /rest/api/3/issue/{key}``; returns ``None`` on the
        Jira 204 success. Raises ``httpx.HTTPStatusError`` on 4xx.
        Display-name keys (``"Revision Target"``) are translated to
        ``customfield_XXXXX`` IDs via ``get_field_map`` (§3.2 M8); keys
        absent from the map pass through so system fields remain keyed
        by their canonical name.
        """
        translated = await self._translate_field_keys(fields)
        await self._request("PUT", f"/rest/api/3/issue/{issue_key}", json={"fields": translated})
