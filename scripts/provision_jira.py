"""Idempotent Jira substrate provisioner (Phase 1 Part A).

Automates the API-automatable subset of ``docs/JiraProvisioningGuide.md``
Part A: creates the two Scrum projects, the 16 custom fields (with
select-list options), one System Config ``Task`` per project labelled
``runner-system``, the seven v0.7.9 saved JQL filters, and the
"Cycle 1 — bootstrap" Sprint on each auto-created Scrum board.

Every action is existence-checked before it is attempted, so reruns
against a partially-provisioned site only create what is still missing.
Manual Part B steps (Rule 3 Automation, §9.1 buttons, board swimlanes)
remain UI-only on Jira Cloud Free and are listed in the final summary.

Cross-reference: ``docs/JiraProvisioningGuide.md`` §§A.1-A.6,
``docs/ExternalRunner.md`` §§3.2-3.3, ``docs/JiraImplementation.md`` §§2, 5, 9.2.

Credentials are resolved via the same pydantic-settings loader pattern
as ``runner.config.Settings``: environment variables (or a local
``.env``) supply defaults; CLI flags override when present. At least
one of each pair (env or flag) must be set or the loader raises
``pydantic.ValidationError`` and the script exits non-zero.

Usage:
    # All via .env (see .env.example)
    python scripts/provision_jira.py

    # Explicit override
    python scripts/provision_jira.py \\
        --jira-url   https://<site>.atlassian.net \\
        --email      <you>@example.com \\
        --token      <API_TOKEN> \\
        --account-id <atlassian_account_id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import AliasChoices, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

# --- Jira custom-field type / searcher constants -------------------------
_SELECT = "com.atlassian.jira.plugin.system.customfieldtypes:select"
_FLOAT = "com.atlassian.jira.plugin.system.customfieldtypes:float"
_DATETIME = "com.atlassian.jira.plugin.system.customfieldtypes:datetime"
_TEXT = "com.atlassian.jira.plugin.system.customfieldtypes:textfield"
_URL = "com.atlassian.jira.plugin.system.customfieldtypes:url"

_S_MULTISEL = "com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher"
_S_NUM = "com.atlassian.jira.plugin.system.customfieldtypes:exactnumber"
_S_DT = "com.atlassian.jira.plugin.system.customfieldtypes:datetimerange"
_S_TEXT = "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher"
_S_EXACT = "com.atlassian.jira.plugin.system.customfieldtypes:exacttextsearcher"


@dataclass(frozen=True)
class FieldSpec:
    """Blueprint for a single Jira custom field per Guide §A.2."""

    name: str
    type_key: str
    searcher: str
    options: tuple[str, ...] = ()


# 17 custom fields per docs/JiraProvisioningGuide.md §A.2.
# "Has Had Test" is modelled as a string-valued single-select with
# options ("false", "true") so the IP-Stale-Eligible JQL clause
# `"Has Had Test" = false` matches via Jira's text-option semantics.
# "Story Points" is runner-owned (distinct from the Jira software-template
# built-in); listing it here guarantees it is attached to every project
# screen via ``ensure_screens`` so operators can tune velocity analytics
# from the UI regardless of the tenant's built-in field-configuration
# scheme.
FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("Stage", _SELECT, _S_MULTISEL, ("Beginner", "Intermediate", "Advanced")),
    FieldSpec("Work Type", _SELECT, _S_MULTISEL, ("Learn", "Revise")),
    FieldSpec("Lifecycle", _SELECT, _S_MULTISEL, ("Active", "Paused", "Archived")),
    FieldSpec("Difficulty", _SELECT, _S_MULTISEL, ("Easy", "Medium", "Hard")),
    FieldSpec("Revision Target", _FLOAT, _S_NUM),
    FieldSpec("Revision Done", _FLOAT, _S_NUM),
    FieldSpec("Outcome", _SELECT, _S_MULTISEL, ("Pass", "Regress")),
    FieldSpec("Has Had Test", _SELECT, _S_MULTISEL, ("false", "true")),
    FieldSpec("Story Points", _FLOAT, _S_NUM),
    FieldSpec("Last Worked At", _DATETIME, _S_DT),
    FieldSpec("Last Transitioned At", _DATETIME, _S_DT),
    FieldSpec("Paused At", _DATETIME, _S_DT),
    FieldSpec("Last Processed Changelog Id", _FLOAT, _S_NUM),
    FieldSpec("Last Successful Poll At", _DATETIME, _S_DT),
    FieldSpec("Last Stale Scan At", _DATETIME, _S_DT),
    FieldSpec("Runner Version", _TEXT, _S_TEXT),
    FieldSpec("Open Alert Issue Url", _URL, _S_EXACT),
)


@dataclass(frozen=True)
class ProjectSpec:
    key: str
    name: str


# Jira Cloud enforces ^[A-Z][A-Z0-9]*$ on project keys, so the Guide's
# informal "CORE-PREP" label maps to "COREPREP" on the wire. Keep the
# constants hyphen-free; any user-facing docs may still read "Core Prep".
PROJECTS: tuple[ProjectSpec, ...] = (
    ProjectSpec("COREPREP", "Core Prep"),
    ProjectSpec("EXTENDED", "Extended Prep"),
)

_SCRUM_TEMPLATE = "com.pyxis.greenhopper.jira:gh-simplified-scrum-classic"


@dataclass(frozen=True)
class FilterSpec:
    name: str
    description: str
    jql: str


# Seven v0.7.9 filters per Guide §A.5. Every non-`IP-Now` filter carries
# the mandatory runner-system exclusion per ExternalRunner §3.3. The
# clause is written as ``(labels IS EMPTY OR labels != "runner-system")``
# because Jira's JQL NULL semantics drop any issue whose ``labels`` is
# unset from ``labels != "..."`` matches (seen during the Sprint 75
# validation: fresh Units with no labels were invisible to dashboards).
FILTERS: tuple[FilterSpec, ...] = (
    FilterSpec(
        "IP-Now",
        "Now / Due — actionable Subtasks (LivingRequirements.md §12.1)",
        'issuetype = Sub-task AND status in ("To Do", "In Progress") '
        "AND (duedate is EMPTY OR duedate <= 3d) "
        "ORDER BY duedate ASC, priority DESC",
    ),
    FilterSpec(
        "IP-Working-Set",
        "Current Working Set — Active Units (LivingRequirements.md §12.2)",
        'issuetype != Sub-task AND "Lifecycle" = "Active" '
        'AND (labels IS EMPTY OR labels != "runner-system") '
        'ORDER BY "Last Worked At" DESC',
    ),
    FilterSpec(
        "IP-Stale",
        "Stale Active Units — 90d idle (LivingRequirements.md §12.3)",
        'issuetype != Sub-task AND "Lifecycle" = "Active" '
        'AND "Last Worked At" <= -90d '
        'AND (labels IS EMPTY OR labels != "runner-system") '
        'ORDER BY "Last Worked At" ASC',
    ),
    FilterSpec(
        "IP-Paused-FIFO",
        "Paused queue — FIFO by Paused At (LivingRequirements.md §12.4)",
        'issuetype != Sub-task AND "Lifecycle" = "Paused" '
        'AND (labels IS EMPTY OR labels != "runner-system") '
        'ORDER BY "Paused At" ASC',
    ),
    FilterSpec(
        "IP-Archive",
        "Archived Units (LivingRequirements.md §12.5)",
        'issuetype != Sub-task AND "Lifecycle" = "Archived" '
        'AND (labels IS EMPTY OR labels != "runner-system") '
        "ORDER BY updated DESC",
    ),
    FilterSpec(
        "IP-Velocity-LT",
        "Progress Velocity source — 30-day Last Transitioned At (LivingRequirements.md §12.6)",
        'issuetype != Sub-task AND "Last Transitioned At" >= -30d '
        'AND (labels IS EMPTY OR labels != "runner-system") '
        'ORDER BY "Last Transitioned At" DESC',
    ),
    FilterSpec(
        "IP-Stale-Eligible",
        "T9 stale-scan eligibility (JiraImplementation.md §9.2 Solo profile)",
        # Has Had Test is modelled as a string-valued single-select with
        # option "false"; the literal must be quoted because bare `false`
        # is a reserved JQL token (Atlassian JQL parser error 400).
        "issuetype != Sub-task AND project in (COREPREP, EXTENDED) "
        'AND "Lifecycle" = "Active" AND "Last Worked At" <= -90d '
        'AND "Has Had Test" = "false" '
        'AND (labels IS EMPTY OR labels != "runner-system") '
        "AND status not in (Done)",
    ),
)

SPRINT_NAME = "Cycle 1 — bootstrap"
SPRINT_GOAL = "Validate Jira Stateless Runner Posture J-C end-to-end on a pilot Unit."
SYSTEM_CONFIG_LABELS = ("runner-system", "hidden")

# Select-list defaults per Guide §A.2 ("default `Active`", etc.). Applied
# via the field-context default-value API so they take effect on new
# issues without a Rule 3 detour. Order is arbitrary; each is idempotent.
FIELD_SELECT_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("Lifecycle", "Active"),
    ("Outcome", "Pass"),
    ("Has Had Test", "false"),
)

# Numeric defaults per Guide §A.2. Best-effort: some Jira Cloud Free
# sites reject float defaults on the context API with a 400, in which
# case the runner falls back to its spec-level default=Target 2
# semantics and this step records the skip in the summary.
FIELD_NUMERIC_DEFAULTS: tuple[tuple[str, float], ...] = (("Revision Done", 0.0),)


# --- Retry policy (shared with runner/jira_client.py) --------------------
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return False


@dataclass
class Summary:
    """Accumulates per-step outcomes for the final report."""

    projects_created: list[str] = field(default_factory=list)
    projects_existed: list[str] = field(default_factory=list)
    fields_created: dict[str, str] = field(default_factory=dict)
    fields_existed: dict[str, str] = field(default_factory=dict)
    options_added: dict[str, list[str]] = field(default_factory=dict)
    system_configs_created: dict[str, str] = field(default_factory=dict)
    system_configs_existed: dict[str, str] = field(default_factory=dict)
    filters_created: dict[str, int] = field(default_factory=dict)
    filters_existed: dict[str, int] = field(default_factory=dict)
    # Filters whose JQL/description was rewritten during this run (only
    # populated when the caller passed ``--update-filters``).
    filters_updated: dict[str, int] = field(default_factory=dict)
    sprints_created: dict[str, int] = field(default_factory=dict)
    sprints_existed: dict[str, int] = field(default_factory=dict)
    sprints_missing_boards: list[str] = field(default_factory=list)
    # Maps screen id -> list of Runner field names attached this run.
    screens_attached: dict[str, list[str]] = field(default_factory=dict)
    # Maps Runner field name -> string repr of the default that is now set.
    defaults_set: dict[str, str] = field(default_factory=dict)
    # Runner field names whose default write was rejected (recorded so the
    # summary can surface a warning without failing the whole run).
    defaults_skipped: dict[str, str] = field(default_factory=dict)


class Provisioner:
    """Owns one authenticated ``httpx.AsyncClient`` and drives Part A."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        account_id: str,
        *,
        update_filters: bool = False,
    ) -> None:
        self._client = client
        self._account_id = account_id
        self._update_filters = update_filters
        self.summary = Summary()
        # Populated by ensure_fields(); consumed by the screen-attach and
        # default-value steps so they don't re-scan the whole site.
        self._field_id_by_name: dict[str, str] = {}

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
        """HTTP request with retry on 429/5xx/transport.

        4xx responses (other than 429) return the response for caller
        inspection — existence checks rely on reading 404 explicitly.
        401/403 are raised as ``PermissionError`` with a hint because
        they almost always indicate a token / site-admin-role problem.
        """
        r = await self._client.request(method, path, params=params, json=json)
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()
        if r.status_code in (401, 403):
            raise PermissionError(
                f"Jira rejected {method} {path} with {r.status_code} — "
                "check the API token scope and site-admin role"
            )
        return r

    # ---- Projects (A.1) -------------------------------------------------
    async def ensure_project(self, spec: ProjectSpec) -> None:
        r = await self._request("GET", f"/rest/api/3/project/{spec.key}")
        if r.is_success:
            self.summary.projects_existed.append(spec.key)
            return
        if r.status_code != 404:
            r.raise_for_status()
        body: dict[str, Any] = {
            "key": spec.key,
            "name": spec.name,
            "projectTypeKey": "software",
            "projectTemplateKey": _SCRUM_TEMPLATE,
            "leadAccountId": self._account_id,
            "assigneeType": "PROJECT_LEAD",
        }
        r2 = await self._request("POST", "/rest/api/3/project", json=body)
        r2.raise_for_status()
        self.summary.projects_created.append(spec.key)

    # ---- Custom fields (A.2-A.3) ---------------------------------------
    async def ensure_fields(self) -> None:
        existing = await self._list_custom_fields()
        for spec in FIELD_SPECS:
            field_id = existing.get(spec.name)
            if field_id is None:
                field_id = await self._create_field(spec)
                self.summary.fields_created[spec.name] = field_id
            else:
                self.summary.fields_existed[spec.name] = field_id
            self._field_id_by_name[spec.name] = field_id
            if spec.options:
                await self._ensure_field_options(spec.name, field_id, spec.options)

    async def _list_custom_fields(self) -> dict[str, str]:
        """Return name -> customfield_XXXXX map for every custom field on the site.

        Uses the paginated ``/rest/api/3/field/search`` endpoint because
        the legacy ``/rest/api/3/field`` reply is silently capped on
        busy sites, which caused ensure_fields to miss existing Runner
        fields and duplicate them on every rerun.
        """
        existing: dict[str, str] = {}
        start_at = 0
        while True:
            r = await self._request(
                "GET",
                "/rest/api/3/field/search",
                params={"maxResults": 50, "startAt": start_at},
            )
            r.raise_for_status()
            body = r.json()
            values = body.get("values", [])
            for item in values:
                if not isinstance(item, dict):
                    continue
                fid = item.get("id")
                name = item.get("name")
                if (
                    isinstance(fid, str)
                    and isinstance(name, str)
                    and fid.startswith("customfield_")
                ):
                    existing[name] = fid
            total = int(body.get("total", 0))
            start_at += len(values)
            if not values or start_at >= total:
                break
        return existing

    async def _create_field(self, spec: FieldSpec) -> str:
        body = {"name": spec.name, "type": spec.type_key, "searcherKey": spec.searcher}
        r = await self._request("POST", "/rest/api/3/field", json=body)
        r.raise_for_status()
        return str(r.json()["id"])

    async def _ensure_field_options(
        self, field_name: str, field_id: str, options: tuple[str, ...]
    ) -> None:
        r = await self._request("GET", f"/rest/api/3/field/{field_id}/context")
        r.raise_for_status()
        contexts = r.json().get("values", [])
        if not contexts:
            return
        context_id = contexts[0]["id"]
        r2 = await self._request("GET", f"/rest/api/3/field/{field_id}/context/{context_id}/option")
        r2.raise_for_status()
        have = {o.get("value") for o in r2.json().get("values", [])}
        missing = [v for v in options if v not in have]
        if not missing:
            return
        r3 = await self._request(
            "POST",
            f"/rest/api/3/field/{field_id}/context/{context_id}/option",
            json={"options": [{"value": v} for v in missing]},
        )
        r3.raise_for_status()
        self.summary.options_added[field_name] = missing

    # ---- Field -> screen attachments (A.3) -----------------------------
    async def ensure_field_screen_attachments(self) -> None:
        """Attach every Runner custom field to each project's active screens.

        POST ``/rest/api/3/filter`` rejects JQL that references custom
        fields which are not on at least one screen visible to the token
        user. This step resolves each project's Issue Type Screen Scheme
        (ISTS) chain and attaches all 16 Runner fields to the first tab
        of every screen in the chain. Idempotent via per-tab field
        enumeration (Guide §A.3).
        """
        for proj in PROJECTS:
            pid = await self._project_id(proj.key)
            if pid is None:
                continue
            screen_ids = await self._project_screen_ids(pid)
            for screen_id in sorted(screen_ids):
                await self._attach_runner_fields_to_screen(screen_id)

    async def _project_id(self, project_key: str) -> str | None:
        r = await self._request("GET", f"/rest/api/3/project/{project_key}")
        if not r.is_success:
            return None
        return str(r.json()["id"])

    async def _project_screen_ids(self, project_id: str) -> set[int]:
        """Return unique screen ids used by a project's non-Bug issuetypes."""
        r = await self._request(
            "GET",
            "/rest/api/3/issuetypescreenscheme/project",
            params={"projectId": project_id},
        )
        r.raise_for_status()
        values = r.json().get("values", [])
        if not values:
            return set()
        ists_id = str(values[0]["issueTypeScreenScheme"]["id"])
        r2 = await self._request(
            "GET",
            "/rest/api/3/issuetypescreenscheme/mapping",
            params={"issueTypeScreenSchemeId": ists_id, "maxResults": 100},
        )
        r2.raise_for_status()
        screen_scheme_ids: set[str] = {
            str(m["screenSchemeId"]) for m in r2.json().get("values", [])
        }
        if not screen_scheme_ids:
            return set()
        r3 = await self._request(
            "GET",
            "/rest/api/3/screenscheme",
            params={"id": sorted(screen_scheme_ids), "maxResults": 100},
        )
        r3.raise_for_status()
        out: set[int] = set()
        for ss in r3.json().get("values", []):
            screens = ss.get("screens", {}) or {}
            for key in ("default", "create", "edit", "view"):
                sid = screens.get(key)
                if isinstance(sid, int):
                    out.add(sid)
                elif isinstance(sid, str) and sid.isdigit():
                    out.add(int(sid))
        return out

    async def _attach_runner_fields_to_screen(self, screen_id: int) -> None:
        r = await self._request("GET", f"/rest/api/3/screens/{screen_id}/tabs")
        if not r.is_success:
            return
        tabs = r.json()
        if not isinstance(tabs, list) or not tabs:
            return
        tab_id = int(tabs[0]["id"])
        r2 = await self._request("GET", f"/rest/api/3/screens/{screen_id}/tabs/{tab_id}/fields")
        if not r2.is_success:
            return
        present: set[str] = {str(f.get("id")) for f in r2.json()}
        added: list[str] = []
        for name, fid in self._field_id_by_name.items():
            if fid in present:
                continue
            r3 = await self._request(
                "POST",
                f"/rest/api/3/screens/{screen_id}/tabs/{tab_id}/fields",
                json={"fieldId": fid},
            )
            if r3.is_success:
                added.append(name)
        if added:
            self.summary.screens_attached[str(screen_id)] = added

    # ---- Field default values (A.2) ------------------------------------
    async def ensure_field_defaults(self) -> None:
        """Set documented default values on select-list and numeric fields.

        Select-list defaults (Lifecycle, Outcome, Has Had Test) are
        mandatory per Guide §A.2; the numeric Revision Done default is
        best-effort. Each PUT is preceded by a GET so reruns against
        already-defaulted contexts become no-ops.
        """
        for name, option_value in FIELD_SELECT_DEFAULTS:
            await self._set_select_default(name, option_value)
        for name, number in FIELD_NUMERIC_DEFAULTS:
            await self._set_numeric_default(name, number)

    async def _set_select_default(self, field_name: str, option_value: str) -> None:
        fid = self._field_id_by_name.get(field_name)
        if fid is None:
            return
        context_id = await self._first_context(fid)
        if context_id is None:
            return
        option_id = await self._find_option_id(fid, context_id=context_id, value=option_value)
        if option_id is None:
            self.summary.defaults_skipped[field_name] = f"option {option_value!r} not found"
            return
        current = await self._current_default(fid)
        if current.get("type") == "option.single" and str(current.get("optionId")) == option_id:
            return
        body = {
            "defaultValues": [
                {"type": "option.single", "contextId": context_id, "optionId": option_id}
            ]
        }
        r = await self._request("PUT", f"/rest/api/3/field/{fid}/context/defaultValue", json=body)
        if r.is_success:
            self.summary.defaults_set[field_name] = option_value
        else:
            self.summary.defaults_skipped[field_name] = f"{r.status_code}: {r.text[:120]}"

    async def _set_numeric_default(self, field_name: str, number: float) -> None:
        fid = self._field_id_by_name.get(field_name)
        if fid is None:
            return
        context_id = await self._first_context(fid)
        if context_id is None:
            return
        current = await self._current_default(fid)
        if current.get("type") == "float" and float(current.get("number", "nan")) == number:
            return
        body = {"defaultValues": [{"type": "float", "contextId": context_id, "number": number}]}
        r = await self._request("PUT", f"/rest/api/3/field/{fid}/context/defaultValue", json=body)
        if r.is_success:
            self.summary.defaults_set[field_name] = str(number)
        else:
            self.summary.defaults_skipped[field_name] = f"{r.status_code}: {r.text[:120]}"

    async def _first_context(self, field_id: str) -> str | None:
        r = await self._request("GET", f"/rest/api/3/field/{field_id}/context")
        if not r.is_success:
            return None
        contexts = r.json().get("values", [])
        if not contexts:
            return None
        return str(contexts[0]["id"])

    async def _find_option_id(self, field_id: str, *, context_id: str, value: str) -> str | None:
        r = await self._request(
            "GET",
            f"/rest/api/3/field/{field_id}/context/{context_id}/option",
        )
        if not r.is_success:
            return None
        for opt in r.json().get("values", []):
            if opt.get("value") == value:
                return str(opt["id"])
        return None

    async def _current_default(self, field_id: str) -> dict[str, Any]:
        r = await self._request("GET", f"/rest/api/3/field/{field_id}/context/defaultValue")
        if not r.is_success:
            return {}
        values = r.json().get("values", [])
        if not values or not isinstance(values[0], dict):
            return {}
        return values[0]

    # ---- System Config issues (A.4) ------------------------------------
    async def ensure_system_configs(self) -> None:
        for proj in PROJECTS:
            existing_key = await self._find_system_config(proj.key)
            if existing_key is not None:
                self.summary.system_configs_existed[proj.key] = existing_key
                continue
            body = {
                "fields": {
                    "project": {"key": proj.key},
                    "issuetype": {"name": "Task"},
                    "summary": f"Runner System Config — {proj.key}",
                    "labels": list(SYSTEM_CONFIG_LABELS),
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "DO NOT EDIT MANUALLY. External Runner state "
                                            "substrate — see docs/ExternalRunner.md §3."
                                        ),
                                    }
                                ],
                            }
                        ],
                    },
                }
            }
            r = await self._request("POST", "/rest/api/3/issue", json=body)
            r.raise_for_status()
            self.summary.system_configs_created[proj.key] = str(r.json()["key"])

    async def _find_system_config(self, project_key: str) -> str | None:
        jql = (
            f'project = "{project_key}" AND labels = "runner-system" '
            'AND summary ~ "Runner System Config"'
        )
        r = await self._request(
            "POST",
            "/rest/api/3/search/jql",
            json={"jql": jql, "maxResults": 1, "fields": ["summary"]},
        )
        r.raise_for_status()
        issues = r.json().get("issues", [])
        if not issues:
            return None
        return str(issues[0]["key"])

    # ---- Saved filters (A.5) -------------------------------------------
    async def ensure_filters(self) -> None:
        """Reconcile the seven saved filters against ``FILTERS``.

        Default behaviour is additive: missing filters are POSTed, existing
        ones are left untouched (name is the primary key). When the caller
        sets ``update_filters=True``, an existing filter whose ``jql`` or
        ``description`` has drifted from the spec is rewritten via
        ``PUT /rest/api/3/filter/{id}``; a filter whose body already
        matches is left alone so reruns are no-ops.
        """
        for spec in FILTERS:
            r = await self._request(
                "GET",
                "/rest/api/3/filter/search",
                params={
                    "filterName": spec.name,
                    "maxResults": 10,
                    "expand": "jql,description",
                },
            )
            r.raise_for_status()
            matches = [f for f in r.json().get("values", []) if f.get("name") == spec.name]
            if matches:
                existing = matches[0]
                filter_id = int(existing["id"])
                if self._update_filters and self._filter_drifted(existing, spec):
                    body = {
                        "name": spec.name,
                        "description": spec.description,
                        "jql": spec.jql,
                    }
                    r_put = await self._request("PUT", f"/rest/api/3/filter/{filter_id}", json=body)
                    r_put.raise_for_status()
                    self.summary.filters_updated[spec.name] = filter_id
                else:
                    self.summary.filters_existed[spec.name] = filter_id
                continue
            body = {"name": spec.name, "description": spec.description, "jql": spec.jql}
            r2 = await self._request("POST", "/rest/api/3/filter", json=body)
            r2.raise_for_status()
            self.summary.filters_created[spec.name] = int(r2.json()["id"])

    @staticmethod
    def _filter_drifted(existing: dict[str, Any], spec: FilterSpec) -> bool:
        """True when the live filter's jql/description differs from ``spec``."""
        return (
            str(existing.get("jql", "")).strip() != spec.jql.strip()
            or str(existing.get("description", "")).strip() != spec.description.strip()
        )

    # ---- Boards + Cycle 1 Sprint (A.6) ---------------------------------
    async def ensure_sprints(self) -> None:
        for proj in PROJECTS:
            r = await self._request(
                "GET",
                "/rest/agile/1.0/board",
                params={"projectKeyOrId": proj.key, "type": "scrum"},
            )
            r.raise_for_status()
            boards = r.json().get("values", [])
            if not boards:
                self.summary.sprints_missing_boards.append(proj.key)
                continue
            board_id = int(boards[0]["id"])
            r2 = await self._request(
                "GET",
                f"/rest/agile/1.0/board/{board_id}/sprint",
                params={"maxResults": 50},
            )
            if r2.status_code == 404 or not r2.is_success:
                self.summary.sprints_missing_boards.append(proj.key)
                continue
            existing = [s for s in r2.json().get("values", []) if s.get("name") == SPRINT_NAME]
            if existing:
                self.summary.sprints_existed[proj.key] = int(existing[0]["id"])
                continue
            body = {
                "name": SPRINT_NAME,
                "originBoardId": board_id,
                "goal": SPRINT_GOAL,
            }
            r3 = await self._request("POST", "/rest/agile/1.0/sprint", json=body)
            r3.raise_for_status()
            self.summary.sprints_created[proj.key] = int(r3.json()["id"])

    # ---- Top-level orchestration ---------------------------------------
    async def run(self) -> None:
        for proj in PROJECTS:
            await self.ensure_project(proj)
        await self.ensure_fields()
        await self.ensure_field_screen_attachments()
        await self.ensure_field_defaults()
        await self.ensure_system_configs()
        await self.ensure_filters()
        await self.ensure_sprints()


# --- Summary reporting ---------------------------------------------------
def _fmt_pairs(label: str, pairs: dict[str, Any]) -> str:
    if not pairs:
        return f"{label}: (none)"
    lines = [f"{label}:"]
    for name, value in pairs.items():
        lines.append(f"  {name:32s} -> {value}")
    return "\n".join(lines)


def print_summary(summary: Summary) -> None:
    print("\n=== Provisioning Summary ===")
    print(f"Projects created:  {summary.projects_created or '(none)'}")
    print(f"Projects existed:  {summary.projects_existed or '(none)'}")
    print(_fmt_pairs("Custom fields created", dict(summary.fields_created)))
    print(_fmt_pairs("Custom fields existed", dict(summary.fields_existed)))
    if summary.options_added:
        print("Options added to existing fields:")
        for name, vals in summary.options_added.items():
            print(f"  {name:32s} += {vals}")
    if summary.screens_attached:
        print("Fields attached to screens:")
        for screen_id, names in summary.screens_attached.items():
            print(f"  screen {screen_id:<8s} += {names}")
    print(_fmt_pairs("Field defaults set", dict(summary.defaults_set)))
    if summary.defaults_skipped:
        print("WARN: field defaults skipped:")
        for name, reason in summary.defaults_skipped.items():
            print(f"  {name:32s} -> {reason}")
    print(_fmt_pairs("System Config issues created", dict(summary.system_configs_created)))
    print(_fmt_pairs("System Config issues existed", dict(summary.system_configs_existed)))
    print(_fmt_pairs("Filters created", dict(summary.filters_created)))
    print(_fmt_pairs("Filters existed", dict(summary.filters_existed)))
    print(_fmt_pairs("Filters updated", dict(summary.filters_updated)))
    print(_fmt_pairs("Sprints created", dict(summary.sprints_created)))
    print(_fmt_pairs("Sprints existed", dict(summary.sprints_existed)))
    if summary.sprints_missing_boards:
        print(f"WARN: no Scrum board found for: {summary.sprints_missing_boards}")
    print("\nRemaining manual steps (Jira Cloud Free UI only — see Guide Part B):")
    print("  1. Create Rule 3 Automation (Lifecycle field-change -> T5/T6/T7/T8) per project")
    print("  2. Add §9.1 Manual-Trigger buttons: Archive, Pause, Resume (per project)")
    print("  3. Configure board swimlanes by Lifecycle (Active / Paused / Archived)")
    print("  4. Tune notification scheme (Issue updated = OFF)")
    print("  5. Run Guide §E verification checklist rows E1-E10 before M11 smoke test")


# --- Configuration -------------------------------------------------------
class ProvisionerSettings(BaseSettings):
    """Env / ``.env`` loader for Jira credentials (CLI-overridable).

    Mirrors the ``runner.config.Settings`` pattern: environment variables
    (case-insensitive) are read, a sibling ``.env`` fills gaps, and
    ``extra`` keys are ignored so the same ``.env`` can feed both the
    runner and this script. ``JIRA_EMAIL`` is the canonical name but
    ``JIRA_USER`` (used by ``runner.config.Settings``) is accepted as an
    alias so a single ``.env`` satisfies both consumers.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    jira_url: str = Field(..., description="Atlassian site URL (https://<site>.atlassian.net).")
    jira_email: str = Field(
        ...,
        validation_alias=AliasChoices("jira_email", "jira_user"),
        description="Atlassian account email; JIRA_USER accepted as alias.",
    )
    jira_token: SecretStr = Field(..., description="Atlassian API token (never logged).")
    jira_account_id: str = Field(
        ..., description="Atlassian accountId (from GET /rest/api/3/myself)."
    )


def _resolve_settings(args: argparse.Namespace) -> ProvisionerSettings:
    """Construct ``ProvisionerSettings`` with CLI values overriding env.

    Only non-``None`` CLI fields are passed as kwargs; absent flags fall
    through to the env / ``.env`` loader. Any missing field then raises
    ``pydantic.ValidationError``, surfaced as a clean exit-2 message.
    """
    overrides: dict[str, Any] = {}
    if args.jira_url is not None:
        overrides["jira_url"] = args.jira_url
    if args.email is not None:
        overrides["jira_email"] = args.email
    if args.token is not None:
        overrides["jira_token"] = args.token
    if args.account_id is not None:
        overrides["jira_account_id"] = args.account_id
    return ProvisionerSettings(**overrides)


# --- CLI -----------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="provision_jira",
        description=(
            "Idempotently provision the Jira Stateless Runner substrate (Phase 1 Part A). "
            "Credentials fall back to environment / .env when flags are omitted."
        ),
    )
    parser.add_argument(
        "--jira-url",
        default=None,
        help="Atlassian site URL (env: JIRA_URL).",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Atlassian account email (env: JIRA_EMAIL, alias JIRA_USER).",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Atlassian API token (env: JIRA_TOKEN).",
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help="Atlassian accountId (env: JIRA_ACCOUNT_ID).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--update-filters",
        action="store_true",
        help=(
            "Rewrite saved filters whose jql/description has drifted from "
            "FILTERS. Without this flag the script only adds missing filters "
            "and leaves existing ones untouched."
        ),
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    try:
        settings = _resolve_settings(args)
    except ValidationError as exc:
        missing = [".".join(str(p) for p in err["loc"]) for err in exc.errors()]
        print(
            "ERROR: missing credentials — set via CLI flag or .env / environment: "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )
        return 2
    auth = httpx.BasicAuth(settings.jira_email, settings.jira_token.get_secret_value())
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
    async with httpx.AsyncClient(
        base_url=settings.jira_url.rstrip("/"),
        auth=auth,
        headers=headers,
        timeout=timeout,
    ) as client:
        prov = Provisioner(
            client,
            settings.jira_account_id,
            update_filters=args.update_filters,
        )
        exit_code = 0
        try:
            await prov.run()
        except PermissionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            exit_code = 2
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:400].replace("\n", " ")
            print(
                f"ERROR: Jira API {exc.request.method} {exc.request.url.path} "
                f"-> {exc.response.status_code}: {body}",
                file=sys.stderr,
            )
            exit_code = 1
        print_summary(prov.summary)
        return exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
