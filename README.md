# jira-stateless-runner

A Python 3.11 asynchronous service that drives a 23-row deterministic state
machine over Jira issues. The runner polls Jira on a 5-minute cron, classifies
changelog events, and emits idempotent side-effects — creating Sub-tasks,
updating custom fields, and posting audit comments — to advance Units through
the `Learn → Revise → Test → Paused/Archived` lifecycle defined in
`temp/LivingRequirements.md §5`.

This repository implements the **Posture J-C** variant of the spec
(Jira-authoritative, Cron-driven) per `temp/ExternalRunner.md`. Milestones
**M1–M10** are landed on `main`.

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
| **System Config issue** (`ztmos-system` label) | Polling watermark + runner version + open-alert URL (§3.2–§3.4) | One-per-project Task issue |
| GitHub Actions Cache (`ztmos-health-state`) | **Ephemeral** health counters only; eviction is self-healing | Never used for correctness-critical state |

The bootstrap self-check (`runner.watermark.check_bootstrap`) enforces that
every user-facing saved filter excludes `labels = "ztmos-system"` and fails
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
`post_comment`, `get_changelog`).

## 4. Key Operational Features

### Idempotency & Audit

- **Idempotency keys** are `sha256(unit_key | event_id | transition_id)[:12]`
  (`runner/idempotency.py`). The 12-hex digest is affixed as an `idem:<hex>`
  label on the Sub-task created by the transition; retries short-circuit on a
  JQL `parent = <unit> AND labels = idem:<hex>` existence check.
- **Audit comments** (`runner/audit.py`) post a Layer-2 `[ZTMOS]` marker on the
  parent Unit carrying the transition-id, key, and the pre/post tuple so every
  state change is self-describing on the Unit page.

### Privacy

`runner/logging_ext.py` implements an **allow-list JSON formatter** per §8.3.
INFO-level records are stripped of any field not in
`_ALLOWED_INFO_FIELDS`; in particular `summary`, `description`, and `comment`
bodies are dropped so world-readable GH Actions logs never leak Unit content.
DEBUG / WARN / ERROR retain the full payload for local troubleshooting.

### Health & Alerting — Dead-Man's-Switch

`runner/health.py` tracks `consecutive_failures` / `recovery_streak`. On the
kind-specific threshold (`http_401`: 1, `http_429`: 5, `http_5xx`: 3,
`logic`: 1) the runner shells out to `gh issue create` opening a GitHub
System Alert; the URL is mirrored into the Jira System Config's
`Open Alert Issue Url` field for durability across cache eviction. Three
consecutive green runs auto-close the alert via `gh issue close`.

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
pytest                       # 142 tests, <1 s
pre-commit run --all-files   # includes gitleaks credential scan
```

CI (`.github/workflows/ci.yml`) runs the identical set on every push and PR.

### Pre-commit hooks

Seven hooks gate every commit: trailing-whitespace, end-of-file,
`check-yaml`, **gitleaks** (credential scan against `.gitleaks.toml`),
`ruff`, `ruff-format`, `mypy`. Bypass is not permitted; false positives
are handled by editing `.gitleaks.toml` allow-lists (see the
`idem[_:]<hex>` entry for precedent).

## 6. Documentation Map

### Normative specifications (`temp/`)

| File | Scope |
|---|---|
| `ExternalRunner.md` | Runner architecture, state substrate, rules, health, privacy — **the authoritative spec** |
| `LivingRequirements.md` | Domain model: Stages, Work Types, Lifecycle, cadence policy, T1–T13 semantics |
| `ImplementationTestMatrix.md` | 23-row parametric truth table (D1–D23) for `tests/test_state_machine.py` |
| `JiraImplementation.md` | Saved-filter JQL, custom-field schema, Solo / Team profiles |
| `JiraProvisioningGuide.md` | Phase 1 human-operator checklist for bootstrap |
| `ImplementationRoadmap.md` | Milestone ordering (M1–M12) and per-milestone deliverables |

When specs disagree with inline code comments, **the specs win**.

### Operator docs (`docs/`)

| File | Scope |
|---|---|
| `DeveloperGuide.md` | CLI cheat sheet for `python -m runner {poll,stale,health}` and local debugging |
