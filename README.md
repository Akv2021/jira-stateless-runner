# jira-stateless-runner

An async Python service that drives a 23-row deterministic state machine over
Jira issues. On a five-minute cron it polls Jira, classifies changelog events,
and emits idempotent side-effects — Sub-task creation, custom-field writes,
audit comments — to move Units through a `Learn → Revise → Test` lifecycle
that ends in `Paused` or `Archived`.

The runner implements the Jira-authoritative, cron-driven variant described
in `docs/ExternalRunner.md`. Milestones M1–M12 are landed on `main` and the
runner has completed live poll-dispatch cycles against the `COREPREP` pilot
project, including the Sprint 75 validation on `COREPREP-3/-4/-5`.

## Project overview

| Concern | Choice |
|---|---|
| Language | Python 3.11 (`asyncio`, PEP 604 unions, strict typing) |
| HTTP | `httpx.AsyncClient` with `tenacity` retries (4 attempts, exponential) |
| Models | Pydantic v2 (`runner/models.py`) |
| Config | `pydantic-settings`, env-driven |
| Runtime | GitHub Actions — `poll-dispatch.yml`, `stale-scan.yml`, `healthcheck.yml` |
| Entrypoint | `python -m runner {poll,stale,health}` |

The state machine in `runner/state_machine.py` is pure: given a
`(Stage, WorkType, Lifecycle, Outcome)` tuple it returns a `TransitionID`
(`T1`–`T13`). All side-effects live in `runner/rules.py`, so the dispatch
table can be unit-tested without Jira.

## Jira as the database

Jira is the only durable state substrate. The runner holds nothing between
polls beyond the HTTP client itself; every restart re-derives its view from
Jira fields.

| Substrate | Purpose | Location |
|---|---|---|
| Custom fields on the Unit (`Stage`, `WorkType`, `Lifecycle`, `Revision Done`, …) | Domain tuple read on every dispatch | Issue payload |
| `idem:<hex>` labels on Sub-tasks | Exactly-once replay guard per transition | Sub-task labels |
| System Config issue (`runner-system` label) | Polling watermark, runner version, open-alert URL | One Task per project |
| GitHub Actions cache (`runner-health-state`) | Ephemeral health counters; eviction self-heals | Never correctness-critical |

`runner.watermark.check_bootstrap` runs on first poll and refuses to proceed
unless every user-facing saved filter excludes `labels = "runner-system"`;
the System Config issue must not leak into user dashboards.

## Architecture

```
          ┌──────────────────────────────────────────────────┐
cron ───▶ │  runner/__main__.py  ── runner/cli.py            │
          │   ├── watermark.read  (System Config fields)     │
          │   ├── ingestor         (raw changelog → events)  │
          │   ├── rules.rule1/2/4  (side-effect handlers)    │
          │   │     └── state_machine.dispatch  (pure logic) │
          │   ├── audit.post       (Layer-2 comment)         │
          │   └── watermark.write  (advance cursor)          │
          └──────────────────┬───────────────────────────────┘
                             │
                   httpx.AsyncClient + tenacity
                             │
                             ▼
                     Jira Cloud REST v3
```

`runner/jira_client.py` is the only module that performs HTTP I/O. Rules and
watermark helpers consume it through `get_issue`, `update_issue`,
`create_subtask`, `search_issues`, `count_issues`, `post_comment`,
`get_changelog`, `iter_changelog_pages`, and `list_comments`.

## Operational features

### Field discovery

Rules are written against display names (`"Stage"`, `"Revision Done"`,
`"Work Type"`); Jira returns and accepts custom fields keyed by opaque
IDs (`customfield_XXXXX`). `JiraClient.get_field_map` fetches
`GET /rest/api/3/field` on first use and caches `{display_name: field_id}`
for the life of the client. Writes are translated display-name → ID, reads
are translated back before reaching callers. System fields (`summary`,
`labels`, `parent`, `duedate`) pass through unchanged.

### Idempotency and audit

Idempotency keys are `sha256(unit_key | event_id | transition_id)[:12]`.
The digest is affixed as an `idem:<hex>` label on the Sub-task created by
the transition; retries short-circuit on a JQL
`parent = <unit> AND labels = idem:<hex>` check. Audit comments
(`runner/audit.py`) post a `[Runner][Tn]` marker on the parent Unit carrying
the transition ID, idempotency key, and the pre/post tuple.

If a prior run created the Sub-task and wrote the fields but crashed before
the audit comment, `audit.comment_exists` detects the missing marker on
the next cycle and re-posts only the comment.

### T1 synthesis

Jira Cloud Free omits the `issue_created` changelog entry for freshly
created issues. `runner/ingestor.py` mints a
`ChangelogEvent(is_new_issue=True)` whenever a polled issue's `created`
timestamp is newer than the watermark but no creation entry appears in its
changelog, so Rule 1 fires regardless of tenant plan.

### 404 handling

`JiraClient._request` turns any `404 Not Found` into
`IssueNotFoundError`. `health.classify` routes it to the `not_found`
bucket with an effectively disabled threshold, so user-initiated mid-flight
deletes do not trip the dead-man's-switch. Rule handlers catch the
exception and emit an `issue_not_found_skip` log so the rest of the batch
proceeds.

### Changelog pagination

`JiraClient.iter_changelog_pages` walks
`/rest/api/3/issue/{key}/changelog`, incrementing `startAt` until Jira
reports `isLast == True` (or a short page is returned on older
deployments). A `page_cap` safety bound raises `RuntimeError` if
pagination runs away. The ingestor consumes the full page list so deep
histories are not silently truncated to the first 100 events.

### Privacy

`runner/logging_ext.py` is an allow-list JSON formatter. INFO records
drop any field outside `_ALLOWED_INFO_FIELDS`; `summary`, `description`,
and `comment` bodies never appear at INFO, so world-readable Actions logs
cannot leak Unit content. DEBUG, WARN, and ERROR retain the full payload
plus `exc_type`, `exc_message`, and `exc_traceback`.

### Health and alerting

`runner/health.py` tracks `consecutive_failures` and `recovery_streak`.
On the kind-specific threshold (`http_401`: 1, `http_429`: 5, `http_5xx`:
3, `logic`: 1, `not_found`: effectively disabled) the runner shells out
to `gh issue create` to open a GitHub System Alert; the URL is mirrored
into the System Config's `Open Alert Issue Url` for durability across
cache eviction. Three consecutive green runs close the alert via
`gh issue close`.

## Development

### Prerequisites

- Python 3.11 (enforced by `pyproject.toml`; `target-version = "py311"`).
- `pip`, or any PEP 621-aware equivalent (`uv`, PDM).
- `gh` CLI, optional, for exercising the alert path locally.

### Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pre-commit install
```

### Quality gates

```bash
ruff check .
ruff format --check .
mypy runner tests
pytest               # 167 tests, sub-second
pre-commit run --all-files
```

CI runs the same commands on every push and PR. The three dispatch
workflows each gate their job on the `gitleaks` action before
`pip install`.

### Pre-commit

Seven hooks: trailing-whitespace, end-of-file, `check-yaml`, `gitleaks`
(credential scan against `.gitleaks.toml`), `ruff`, `ruff-format`,
`mypy`. Bypass is not permitted; false positives are handled by editing
`.gitleaks.toml` allow-lists — see the `idem[_:]<hex>` entry for
precedent.

## Documentation

`docs/` is authoritative for behaviour, provisioning, and milestone
plan.

| File | Scope |
|---|---|
| `ExternalRunner.md` | Runner architecture, state substrate, rules, health, privacy |
| `LivingRequirements.md` | Domain model: Stages, Work Types, Lifecycle, cadence, T1–T13 |
| `ImplementationTestMatrix.md` | 23-row truth table (D1–D23) consumed by `tests/test_state_machine.py` |
| `JiraImplementation.md` | Saved-filter JQL, custom-field schema, Solo / Team profiles |
| `JiraProvisioningGuide.md` | Operator checklist — API-automated and manual steps |
| `ImplementationRoadmap.md` | Milestone ordering (M0–M12) and deliverables |
| `DayToDay.md` | Worked examples of the Unit lifecycle |
| `DeveloperGuide.md` | CLI cheat sheet and local debugging |
| `ManualConfig.md` | Jira configuration that is not API-automatable |
| `ExtendedPendingRules.md` | Backlog of rules deferred past M12 |

When the specs disagree with inline code comments, the specs win.
