# jira-stateless-runner

A Python 3.11 asynchronous service that drives a 23-row deterministic state
machine over Jira issues. The runner polls Jira on a 5-minute cron, classifies
changelog events, and emits idempotent side-effects — creating Sub-tasks,
updating custom fields, and posting audit comments — to advance Units through
the `Learn → Revise → Test → Paused/Archived` lifecycle defined in
`docs/LivingRequirements.md §5`.

This repository implements the **Posture J-C** variant of the spec
(Jira-authoritative, Cron-driven) per `docs/ExternalRunner.md`. Milestones
**M1–M12** are landed on `main`; the runner has executed a successful live
poll-dispatch against the pilot `COREPREP` project.

## 1. Project Overview

| Concern | Choice |
|---|---|
| Language | Python **3.11** (`asyncio`, PEP 604 union syntax, strict typing) |
| HTTP | `httpx.AsyncClient` with `tenacity`-backed retries (4 attempts, exponential) |
| Models | Pydantic **v2** (`runner/models.py`) |
| Config | `pydantic-settings` loading env vars with eager validation |
| Runtime | GitHub Actions (`poll-dispatch.yml`, `stale-scan.yml`, `healthcheck.yml`) |
| Entrypoint | `python -m runner {poll,stale,health}` |

The state machine (`runner/state_machine.py`) is **pure** — it consumes a
`(Stage, WorkType, Lifecycle, Outcome)` tuple and emits a `TransitionID`
(`T1`–`T13`). Every side-effect lives behind the Logic Engine boundary in
`runner/rules.py`, making the dispatch table testable without Jira in the loop.

## 2. Core Philosophy — "Jira as the database"

The **sole authoritative state substrate is Jira**. The runner is stateless by
construction; every restart re-derives its worldview from Jira fields.

| Substrate | Purpose | Location |
|---|---|---|
| Custom fields on the Unit (`Stage`, `WorkType`, `Lifecycle`, `Revision Done`, …) | Domain tuple read on every dispatch | Issue payload |
| `idem:<hex>` Jira labels on Sub-tasks | Exactly-once replay guard per transition (§5.3) | Sub-task labels |
| **System Config issue** (`runner-system` label) | Polling watermark + runner version + open-alert URL (§3.2–§3.4) | One-per-project Task issue |
| GitHub Actions Cache (`runner-health-state`) | **Ephemeral** health counters only; eviction is self-healing | Never used for correctness-critical state |

The bootstrap self-check (`runner.watermark.check_bootstrap`) enforces that
every user-facing saved filter excludes `labels = "runner-system"` and fails
fast with `BootstrapIncompleteError` on first run otherwise.

## 3. System Architecture

```
          ┌──────────────────────────────────────────────────┐
cron ───▶ │  runner/__main__.py  ── runner/cli.py            │
          │   ├── watermark.read  (System Config fields)     │
          │   ├── ingestor         (raw changelog → events)  │
          │   ├── rules.rule1/2/4  (side-effect handlers)    │
          │   │     └── state_machine.dispatch  (pure logic) │
          │   ├── audit.post       (§5.2 Layer-2 comment)    │
          │   └── watermark.write  (advance cursor)          │
          └──────────────────┬───────────────────────────────┘
                             │
                   httpx.AsyncClient + tenacity
                             │
                             ▼
                     Jira Cloud REST v3
```

`runner/jira_client.py` is the only module that performs HTTP I/O. Rules and
watermark helpers consume it through async methods (`get_issue`,
`update_issue`, `create_subtask`, `search_issues`, `count_issues`,
`post_comment`, `get_changelog`, `iter_changelog_pages`, `list_comments`).

## 4. Key Operational Features

### Field Discovery Bootstrap (M8)

Jira v3 returns and accepts custom fields keyed by opaque IDs
(`customfield_XXXXX`), but every rule in `runner/rules.py` is written
against human-readable display names (`"Stage"`, `"Revision Done"`,
`"Work Type"`). `JiraClient.get_field_map` lazy-loads
`GET /rest/api/3/field` on first use and caches `{display_name: field_id}`
for the life of the client; writes (`update_issue`, `create_subtask`)
are translated display-name → ID, and reads (`get_issue`,
`search_issues`) are translated back ID → display-name before the
payload reaches the caller. System fields (`summary`, `labels`,
`parent`, `duedate`) pass through unchanged.

### Idempotency & Audit

- **Idempotency keys** are `sha256(unit_key | event_id | transition_id)[:12]`
  (`runner/idempotency.py`). The 12-hex digest is affixed as an `idem:<hex>`
  label on the Sub-task created by the transition; retries short-circuit on a
  JQL `parent = <unit> AND labels = idem:<hex>` existence check.
- **Audit comments** (`runner/audit.py`) post a Layer-2 `[Runner]` marker on the
  parent Unit carrying the transition-id, key, and the pre/post tuple so every
  state change is self-describing on the Unit page.
- **Partial-success replay (§6.6):** if a prior run created the Sub-task +
  wrote the fields but crashed before the audit comment POST,
  `audit.comment_exists` detects the missing `idem_<hex>` marker on the
  next cycle and re-posts the comment without re-emitting the Sub-task.

### 404 Handling

`JiraClient._request` translates any `404 Not Found` into a dedicated
`IssueNotFoundError`. `health.classify` routes it into the `not_found`
bucket with a sentinel threshold, so user-initiated mid-flight deletes
never trip the dead-man's-switch alert. Rule handlers catch the
exception and emit an `issue_not_found_skip` log so the rest of the
poll batch proceeds.

### Multi-page Changelog Walking

`JiraClient.iter_changelog_pages` paginates
`/rest/api/3/issue/{key}/changelog` by incrementing `startAt` until
Jira reports `isLast == True` (or a short page is returned on older
deployments), with a `page_cap` safety bound that raises
`RuntimeError` if pagination runs away. The CLI ingestor consumes the
full page list so issues with deep histories are not silently
truncated to the first 100 events.

### Privacy

`runner/logging_ext.py` implements an **allow-list JSON formatter** per §8.3.
INFO-level records are stripped of any field not in
`_ALLOWED_INFO_FIELDS`; in particular `summary`, `description`, and `comment`
bodies are dropped so world-readable GH Actions logs never leak Unit content.
DEBUG / WARN / ERROR retain the full payload plus `exc_type` / `exc_message`
/ `exc_traceback` fields for diagnosable failure modes.

### Health & Alerting — Dead-Man's-Switch

`runner/health.py` tracks `consecutive_failures` / `recovery_streak`. On the
kind-specific threshold (`http_401`: 1, `http_429`: 5, `http_5xx`: 3,
`logic`: 1, `not_found`: effectively disabled) the runner shells out to
`gh issue create` opening a GitHub System Alert; the URL is mirrored into
the Jira System Config's `Open Alert Issue Url` field for durability across
cache eviction. Three consecutive green runs auto-close the alert via
`gh issue close`.

## 5. Development Setup

### Prerequisites

- Python **3.11** (checked by `pyproject.toml` — `target-version = "py311"`).
- `pip` (PDM or `uv` work equivalently if preferred; the project is PEP 621).
- Optional: `gh` CLI for exercising the health-alert path locally.

### Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pre-commit install
```

### Quality Gates (all must pass before every commit)

```bash
ruff check .                 # lint
ruff format --check .        # format
mypy runner tests            # strict type-check
pytest                       # 154 tests, <1 s
pre-commit run --all-files   # includes gitleaks credential scan
```

CI (`.github/workflows/ci.yml`) runs the identical set on every push and PR.
The three dispatch workflows (`poll-dispatch.yml`, `stale-scan.yml`,
`healthcheck.yml`) each gate their job on the gitleaks action before
`pip install -e .` per §8.2.

### Pre-commit hooks

Seven hooks gate every commit: trailing-whitespace, end-of-file,
`check-yaml`, **gitleaks** (credential scan against `.gitleaks.toml`),
`ruff`, `ruff-format`, `mypy`. Bypass is not permitted; false positives
are handled by editing `.gitleaks.toml` allow-lists (see the
`idem[_:]<hex>` entry for precedent).

## 6. Documentation Map

All operational and normative content lives under `docs/`; `docs/` is the
single authoritative source for the runner's behaviour, provisioning
procedure, and milestone plan.

| File | Scope |
|---|---|
| `ExternalRunner.md` | Runner architecture, state substrate, rules, health, privacy — **the authoritative spec** |
| `LivingRequirements.md` | Domain model: Stages, Work Types, Lifecycle, cadence policy, T1–T13 semantics |
| `ImplementationTestMatrix.md` | 23-row parametric truth table (D1–D23) for `tests/test_state_machine.py` |
| `JiraImplementation.md` | Saved-filter JQL, custom-field schema, Solo / Team profiles |
| `JiraProvisioningGuide.md` | Phase 1 operator checklist (API-automated + manual UI steps) |
| `ImplementationRoadmap.md` | Milestone ordering (M0–M12) and per-milestone deliverables |
| `ExtendedPendingRules.md` | Backlog of rules deferred past M12 |
| `ManualConfig.md` | Non-API-automatable Jira configuration (screen schemes, etc.) |
| `DeveloperGuide.md` | CLI cheat sheet for `python -m runner {poll,stale,health}` and local debugging |

When specs disagree with inline code comments, **the specs win**.
