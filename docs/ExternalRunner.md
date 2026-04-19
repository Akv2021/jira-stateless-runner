# Jira Stateless Runner External Runner Profile — Posture J-C Technical Specification

> **Status:** Non-normative reference · v0.1.1 · **Last updated:** 2026-04-18 · **Source of truth:** [`LivingRequirements.md`](./LivingRequirements.md) v0.7.8, [`JiraImplementation.md`](./JiraImplementation.md) v0.7.8 §9

This document specifies the GitHub-side implementation of **Posture J-C** — the zero-cost hybrid architecture in which Jira Cloud Free hosts state and GitHub Actions executes the T1–T13 state-machine logic of [`LivingRequirements.md §5`](./LivingRequirements.md). It is an **additive delta** over [`JiraImplementation.md §9 Solo-User Profile`](./JiraImplementation.md); nothing here alters the normative state machine or the Jira configuration in §§1–8 of that file.

**Authority rule:** as with [`JiraImplementation.md`](./JiraImplementation.md), this document adds no requirements. If any row conflicts with [`LivingRequirements.md §§1–13`](./LivingRequirements.md), the main document wins. The choice of Python, GitHub Actions, cron polling, and Jira-as-watermark-store is a platform-mapping decision; a solo developer may substitute any runtime (Node.js, Go, Cloudflare Worker, self-hosted cron) that preserves the contract in §1 below.

**Motivation recap:** [`JiraImplementation.md §9`](./JiraImplementation.md) establishes that Jira Free's 100-runs-per-month automation cap cannot host the full T1–T13 chain at the described workload (3–5 new Units/day × ~4 transitions per lifecycle = ~260–680 runs/month). The External Runner relocates Rules 1, 2, and 4 out of Jira Automation into GitHub Actions, consuming zero Jira Automation quota and preserving the full user-facing UX including the §9.1 Manual-Trigger buttons.

---

## 1. Architecture Overview

### 1.1 Responsibility split

The system is partitioned along a single clean seam: **Jira owns state; GitHub owns logic.**

| Layer | Substrate | Owns | Does not own |
|---|---|---|---|
| **State Substrate** | Jira Cloud Free | All custom fields (`Stage`, `WorkType`, `Lifecycle`, `RevisionDone`, `RevisionTarget`, `Outcome`, `Has Had Test`, `LastSubtaskCompletedAt`, `LastTransitionedAt`, `PausedAt`); issue hierarchy (Unit → Subtask); the Status × Lifecycle board ([`JiraImplementation.md §6.1`](./JiraImplementation.md)); Sprint as Working Set ([`JiraImplementation.md §6.0`](./JiraImplementation.md)); the three §9.1 Manual-Trigger buttons (Archive, Pause, Resume); the audit-comment timeline on each Unit | Transition logic; date arithmetic; scheduled scans |
| **Logic Engine** | GitHub Actions | Rule 1 (T1 on Unit-created); Rule 2 (T2/T3/T4/T12/T13 dispatch on Subtask→Done); Rule 4 (T9 weekly stale scan); `RevisionGap[n] ∈ {2,5,11,25}` business-day arithmetic; idempotency-key computation; audit-comment emission; dead-man's-switch monitoring | Any user-facing state; any board configuration; any custom-field definition |

Rule 3 (Lifecycle field change → T5/T6/T7/T8) **remains in Jira Automation**, because the §9.1 Manual-Trigger buttons already write the `Lifecycle` field — the button click and the T5/T6/T7/T8 firing are the same event, not additive. A small residual Jira Automation footprint (~15 runs/month) for §9.1 alone preserves the button UX without threatening the 100-run cap.

### 1.2 Invariants preserved from the normative spec

| Invariant | Source | How preserved here |
|---|---|---|
| Dispatch first-match-wins (Regress before Pass) | [`LivingRequirements.md §5.5`](./LivingRequirements.md) | `state_machine.py` match-case ordering (§2.3 below) |
| Single-writer per state-tuple field | [`LivingRequirements.md §5.4`](./LivingRequirements.md) | `rules.py` is the only module that writes tuple fields; `jira_client.py` offers the primitive but `rules.py` gates the call |
| T9 lifetime idempotency | [`LivingRequirements.md §5.2`](./LivingRequirements.md) T9 | `Has Had Test` Boolean flag, set-once-never-cleared by `rule4_stale_scan`; enforced in the JQL pre-filter |
| T13 does not re-arm T9 | [`LivingRequirements.md §5.2`](./LivingRequirements.md) T13 | `rule2_subtask_done` never clears `Has Had Test` on the T13 branch |
| Subtask.Outcome sealed on dispatch | [`LivingRequirements.md §5.4`](./LivingRequirements.md) Rule 4 | Idempotency label on the created successor Subtask locks the decision; retries are no-ops |
| Default Outcome = Pass preserves v0.7.5 behaviour | [`LivingRequirements.md §5.5`](./LivingRequirements.md) | Missing-field in Jira is read as `Pass` by `jira_client.py` |

### 1.3 High-level trigger surface

```
┌─────────────────────────┐
│  Jira Cloud Free        │
│  ┌───────────────────┐  │
│  │  User actions     │  │     ①   ②   ③   ④
│  │  • Create Unit    │  │     │   │   │   │
│  │  • Mark Subtask   │  │     │   │   │   │
│  │    Done           │  │     │   │   │   │
│  │  • Click §9.1 btn │  │     ▼   ▼   ▼   ▼
│  └───────────────────┘  │    (changelog stream, polled every 5 min)
│           │             │     │
│           ▼             │     │
│  ┌───────────────────┐  │     │
│  │  Rule 3 (§9.1)    │  │     │
│  │  remains in Jira  │  │     │
│  │  Automation       │  │     │
│  └───────────────────┘  │     │
└─────────────────────────┘     │
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
       ┌──────────────────┐          ┌──────────────────┐
       │  poll-dispatch   │          │   stale-scan     │
       │  cron: */5 * * * *│         │   cron: 0 10 * * MON│
       │  Rules 1 + 2     │          │   Rule 4 (T9)    │
       └──────────────────┘          └──────────────────┘
                 │                             │
                 └──────────────┬──────────────┘
                                ▼
                 ┌────────────────────────────┐
                 │  Jira REST API writes:     │
                 │  • Create Subtask          │
                 │  • Edit Unit tuple fields  │
                 │  • Post audit comment      │
                 │  • Set Has Had Test (T9)   │
                 │  • Write watermark (§3)    │
                 └────────────────────────────┘
```

Events ①–④ are user-triggered changes in Jira; the polling workflow reads them from Jira's changelog API every five minutes and dispatches the appropriate transition.

### 1.4 Non-goals

- **No Jira UI modifications.** The External Runner never creates screens, fields, or workflow states; the user's Jira experience is identical to [`JiraImplementation.md §§1–9`](./JiraImplementation.md).
- **No cross-Unit inference.** The runner processes one event at a time; bulk operations are Jira-native (e.g., sprint start/complete).
- **No content generation.** Interview-prep content (problems, concepts, solutions) continues to live in GitHub per [`JiraImplementation.md §7`](./JiraImplementation.md); the runner reads titles and metadata only.
- **No webhook relay.** Push-delivery is deferred to [§7 Future Scope](#7-future-scope); the zero-infrastructure polling model is the primary path.

---

## 2. Repository Structure

A single GitHub repository — referenced below as `jira-stateless-runner` — houses the Logic Engine.

### 2.1 Directory layout

```
jira-stateless-runner/
├── .github/
│   └── workflows/
│       ├── poll-dispatch.yml        # §4.1 — cron every 5 min; Rules 1 + 2
│       ├── stale-scan.yml           # §4.2 — cron Mon 10:00 local; Rule 4 (T9)
│       └── healthcheck.yml          # §6.5 — cron every 6 h; dead-man's-switch watchdog
├── runner/
│   ├── __init__.py
│   ├── __main__.py                  # CLI: `python -m runner {poll,stale,health}`
│   ├── config.py                    # RevisionGap, RevisionTarget, StaleDays, thresholds
│   ├── models.py                    # @dataclass Unit, Subtask, TransitionEvent, Watermark
│   ├── jira_client.py               # §5.1 retry/backoff wrapper over Jira REST
│   ├── state_machine.py             # §2.3 — pure dispatch(tuple, outcome) → TransitionID
│   ├── rules.py                     # §4 — rule1_unit_created, rule2_subtask_done, rule4_stale_scan
│   ├── idempotency.py               # §5.3 — compute_key(), has_been_applied(), mark_applied()
│   ├── audit.py                     # §5.2 — layer-2 audit-comment emission on Unit
│   ├── watermark.py                 # §3 — read/write Jira System Config issue
│   ├── health.py                    # §6 — dead-man's-switch state machine
│   └── logging_ext.py               # §5.1 — structured JSON logger with run/event/unit propagation
├── tests/
│   ├── test_state_machine.py        # parametric from ImplementationTestMatrix.md §3 (D1–D23)
│   ├── test_rules.py
│   ├── test_idempotency.py
│   ├── test_watermark.py
│   └── fixtures/jira_payloads/*.json
├── pyproject.toml                   # deps: httpx, tenacity, pydantic, python-dateutil, pytest
└── README.md                        # setup: secrets, Jira PAT, System Config bootstrap
```

### 2.2 Module responsibility matrix

| Module | Responsibility | Spec reference |
|---|---|---|
| `state_machine.py` | **Pure** function `(Stage, WorkType, Lifecycle, RevisionDone, RevisionTarget, Outcome) → TransitionID ∈ {T1…T13, NOOP}`. Zero I/O. | [`LivingRequirements.md §5.2`](./LivingRequirements.md), [`§5.5`](./LivingRequirements.md); [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md) rows D1–D23 |
| `rules.py` | Side-effecting handlers — one per Rule 1/2/4. Reads current state from Jira, calls `state_machine.dispatch`, writes post-state + successor Subtask + audit comment. | [`JiraImplementation.md §4.1, §4.2, §4.4`](./JiraImplementation.md) |
| `config.py` | Central config per FR4. `RevisionGap = [2, 5, 11, 25]`; `RevisionTarget = {Easy: 2, Medium: 3, Hard: 4}`; **`RevisionTargetDefault = 2`** (applied when `Difficulty` is null/missing at Unit creation — see §4.1 Rule 1 fallback); `StaleDays = 90`; dead-man's-switch thresholds. The **only** place these constants live. | [`LivingRequirements.md §6 FR4`](./LivingRequirements.md); [`JiraImplementation.md §2`](./JiraImplementation.md) (Difficulty → RevisionTarget seeding) |
| `jira_client.py` | Single chokepoint for all Jira REST calls. Centralises `tenacity` retry, 429 back-off, 401 detection, dead-man's-switch signalling. | §5.1, §6 below |
| `idempotency.py` | Computes `sha256(unit_key │ event_id │ transition_id)` as the idempotency key; reads/writes the `idem:<key>` label on created Subtasks. | §5.3 below |
| `audit.py` | Formats and posts the canonical audit-comment (§5.2) on the parent Unit. Idempotent by label-suffix match. | §5.2 below |
| `watermark.py` | Reads/writes the Jira System Config issue (§3). Single source of truth for `last_processed_changelog_id`. | §3 below |
| `health.py` | Dead-man's-switch state machine — counts consecutive failures, opens/closes `gh issue` alerts. | §6 below |
| `logging_ext.py` | Structured JSON logger. Every log line carries `run_id`, `event_id`, `unit_key`, `transition_id` for traceability (§5.1). | §5 below |

### 2.3 State-machine dispatch (Python 3.11 structural pattern matching)

The T1–T13 dispatch logic is a 23-row table keyed on the state tuple and `Outcome` — a canonical structural-match workload. Python 3.10+ `match/case` produces code whose arms align 1:1 with [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md) dispatch rows, making spec↔code review a visual exercise:

```python
def dispatch(
    stage: Stage,
    work_type: WorkType,
    lifecycle: Lifecycle,
    rev_done: int,
    rev_target: int,
    outcome: Outcome | None,
) -> TransitionID:
    """Pure dispatch. Regress branches evaluated FIRST per §5.5 first-match-wins."""
    match (stage, work_type, lifecycle, outcome):
        # --- T12 / T13: Regress branches (§5.5 evaluated first) ---
        case (_, "Revise", "Active", "Regress"):
            return "T12"
        case (_, "Test",   "Active", "Regress"):
            return "T13"
        # --- T2: Learn → Revise#1 ---
        case (_, "Learn",  "Active", "Pass" | None):
            return "T2"
        # --- T3 / T4: Revise chain progression or Pause-at-Target ---
        case (_, "Revise", "Active", "Pass" | None) if rev_done + 1 <  rev_target:
            return "T3"
        case (_, "Revise", "Active", "Pass" | None) if rev_done + 1 >= rev_target:
            return "T4"
        # --- D1 / D3: Test → Done with Pass is LSC-only (no tuple motion) ---
        case (_, "Test",   "Active", "Pass" | None):
            return "NOOP"
        case _:
            raise UnreachableState(stage, work_type, lifecycle, outcome, rev_done, rev_target)
```

**Why Python 3.11 (preserved from the refinement evaluation):**

1. **Match/case dispatch** encodes the matrix declaratively; the Node/TS alternatives require mentally rebuilding the grid on each review.
2. **`tenacity`** offers decorator-based retry with declarative predicates (`retry_if_exception_type`) — Node's `p-retry` wrapper entangles call graphs.
3. **Ecosystem stability** — `httpx`, `tenacity`, `pydantic`, `python-dateutil` ship majors every 18–36 months with long compat windows. A multi-year personal project touched a few times per year is likelier to still work on first `pip install` with Python than Node's faster-churning ecosystem.
4. **Stdlib coverage** — `datetime`, `hashlib`, `json`, `logging`, `argparse` all in the stdlib; dependency surface minimised.

### 2.4 `pyproject.toml` dependency set

```toml
[project]
name = "jira-stateless-runner"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",          # HTTP client with HTTP/2 + async
    "tenacity>=8.2",        # retry decorators (§5.1, §6)
    "pydantic>=2.5",        # TransitionEvent / Unit / Subtask models
    "python-dateutil>=2.8", # business-day arithmetic for RevisionGap
]

[project.optional-dependencies]
dev = ["pytest>=7.4", "pytest-httpx>=0.30", "ruff>=0.3"]

[project.scripts]
runner = "runner.__main__:main"
```

No framework (Flask, Django, FastAPI) is introduced — the runner is a batch script, not a service.

---


## 3. State Management — Jira System Config Issue

The runner is **stateless** from GitHub's perspective: a fresh `git clone` of `jira-stateless-runner` on a new machine resumes operation with zero data loss. All persistent state — the polling watermark, health timestamps, runner version — lives in Jira, alongside the Units it describes.

### 3.1 Why Jira and not GitHub Actions Cache

The decision is recorded here to survive repo migrations and future re-evaluations. Two considerations are decisive:

| Property | GH Actions Cache | Jira System Config issue |
|---|---|---|
| Eviction policy | 7 days idle, OR LRU when repo cache > 10 GB | None — persists for the life of the project |
| Survives user break (> 7 days) | ❌ Cache evicted; runner must fall back to "process last N days" which either loses events or explodes the job timeout | ✅ Watermark still points to the last processed event; runner processes exactly the real backlog |
| Survives repo wipe / re-clone | ❌ | ✅ |
| Aligns with "State Substrate = Jira" principle | ❌ Two substrates | ✅ Single substrate |
| Auditability by the user | ❌ Cache is not user-browsable | ✅ Open the issue, read the field |
| Manually rewindable (e.g. to re-run a missed event) | Requires workflow re-run with cache-clear step | User edits the field; next run resumes from there |

The "user takes a break" case is the critical one: interview-prep workflows have predictable break patterns (vacation, offer acceptance, travel) of more than 7 days, during which GH Cache would evict silently and the runner would wake up without a watermark. Jira-as-watermark survives all of these.

### 3.2 System Config issue design

One System Config issue per project (`Core`, `Extended`, `Independent-*`) — scoped to the project because each has its own changelog stream and staleness semantics.

| Field | Type | Writer | Purpose |
|---|---|---|---|
| **Summary** | `Runner System Config — <project-key>` | Manual (one-time bootstrap) | Human identifier |
| **Issue Type** | `Task` (or custom `System` if available) | Bootstrap | Filtered-out of all user-facing views (§3.3) |
| **Labels** | `runner-system, hidden` | Bootstrap | Filter marker |
| `Last Processed Changelog Id` | Number (long) | `poll-dispatch` at end of run | Primary watermark — see §3.4 |
| `Last Successful Poll At` | DateTime | `poll-dispatch` at end of run | Input to healthcheck staleness detection (§6.5) |
| `Last Stale Scan At` | DateTime | `stale-scan` at end of run | Guards against double-running Rule 4 |
| `Runner Version` | String (short text) | `poll-dispatch` at start | Detects out-of-sync deployments |
| `Open Alert Issue Url` | URL | `health.py` | Pointer to the currently-open GitHub alert, if any (§6.4) |

### 3.3 View-filter requirement — **mandatory bootstrap step**

Because the System Config issue shares an `issuetype` with Unit-bearing issues in some projects, **every user-facing JQL view in [`JiraImplementation.md §5`](./JiraImplementation.md) MUST be amended** to exclude it. This is a **blocking bootstrap step**, not an optional hardening: without it, the System Config issue would surface in the user's Working Set, Stale, Paused, Archive, and Velocity views, polluting every report with a non-Unit artefact and breaking the invariant that §5 filters enumerate execution artefacts only.

**Mandatory addition to every affected filter:**

```
AND labels != "runner-system"
```

Affected saved filters (bootstrap owner must amend **all non-safe** rows before the first `workflow_dispatch` run):

| Filter | Current ([`JiraImplementation.md §5`](./JiraImplementation.md)) | Mandatory addition | Bootstrap status |
|---|---|---|---|
| `IP-Now` | `issuetype = Sub-task AND status in ...` | — | ✅ Already safe — Sub-task filter excludes System Config |
| `IP-Working-Set` | `issuetype != Sub-task AND "Lifecycle" = "Active"` | `AND labels != "runner-system"` | ⛔ **Required** |
| `IP-Stale` | `issuetype != Sub-task AND "Lifecycle" = "Active" AND "Last Worked At" <= -90d` | `AND labels != "runner-system"` | ⛔ **Required** |
| `IP-Paused-FIFO` | `issuetype != Sub-task AND "Lifecycle" = "Paused"` | `AND labels != "runner-system"` | ⛔ **Required** |
| `IP-Archive` | `issuetype != Sub-task AND "Lifecycle" = "Archived"` | `AND labels != "runner-system"` | ⛔ **Required** |
| `IP-Velocity-LT` | `issuetype != Sub-task AND "Last Transitioned At" >= -30d` | `AND labels != "runner-system"` | ⛔ **Required** |
| `IP-Stale-Eligible` | `issuetype != Sub-task AND "Lifecycle" = "Active" AND ...` | `AND labels != "runner-system"` | ⛔ **Required** |

**Bootstrap enforcement:** the runner's `python -m runner poll` entrypoint executes a one-time self-check on first run — it queries each filter by name and fails fast with a `BootstrapIncompleteError` (and a Layer 1 `ERROR` log naming the unamended filters) if any view still matches the System Config issue. This self-check makes the bootstrap step both **mandatory in policy** and **enforced in code**, aligning with the Solo-User Profile's low-cognitive-load principle ([`JiraImplementation.md §9`](./JiraImplementation.md)) by surfacing omissions at setup rather than at report-time.

### 3.4 Watermark semantics

Jira's `/rest/api/3/search` endpoint with JQL `updated > <timestamp>` does not expose changelog IDs directly, so the runner uses the per-issue changelog endpoint `/rest/api/3/issue/{issueIdOrKey}/changelog` and tracks the maximum `changelog.id` seen across all issues in the project.

**Read pattern (start of every poll run):**
```python
config = jira_client.get_issue(f"{project_key}-SYSTEM-1")
watermark = config.fields["Last Processed Changelog Id"] or 0
runner_version_in_jira = config.fields["Runner Version"]
```

**Write pattern (end of every successful poll run):**
```python
jira_client.update_issue(
    f"{project_key}-SYSTEM-1",
    fields={
        "Last Processed Changelog Id": max_changelog_id_seen,
        "Last Successful Poll At": datetime.utcnow().isoformat(),
        "Runner Version": __version__,
    },
)
```

**Write is atomic from the runner's perspective** — either the Jira `PUT /issue/{key}` succeeds (watermark advances) or fails (watermark unchanged; next run re-processes the batch, idempotency labels absorbing the duplicates per §5.3).

### 3.5 GitHub Actions Cache — residual ephemeral scratch

GH Cache is **not eliminated**, only demoted. It retains two narrow uses where loss-on-eviction is self-healing:

| Key | Content | Consequence of eviction |
|---|---|---|
| `runner-health-state` | `{consecutive_failures, recovery_streak, last_failure_kind, open_alert_issue}` JSON | Counter resets to 0; next failure re-starts the count. No lost alerts (open alert ID is also in Jira System Config). |
| `runner-poisoned-events` | List of event IDs that have failed 3 consecutive runs | Re-enters normal processing; if truly poisoned, will re-quarantine within 3 runs. |

Both are performance / UX optimisations, not correctness-critical state.

---

## 4. Automation Logic

### 4.1 `poll-dispatch.yml` — 5-minute polling (Rules 1 and 2)

**Architectural necessity:** GitHub Actions cannot natively listen for inbound HTTP POST requests. The three inbound-event surfaces it exposes are `schedule` (cron), `workflow_dispatch`, and `repository_dispatch` — the latter two being REST endpoints on `api.github.com` that require a GitHub PAT as `Authorization: Bearer <pat>`. Jira's native outgoing webhooks do not support configurable auth headers, and the Jira Automation "Send Web Request" action that *does* support them would consume the 100-run/month quota the External Runner was designed to escape. **Cron polling is therefore the only zero-infrastructure, zero-Jira-quota event-ingest mechanism.** See [§7 Future Scope](#7-future-scope) for the Cloudflare Worker relay path.

**Workflow:**

```yaml
name: runner-poll-dispatch
on:
  schedule: [{ cron: '*/5 * * * *' }]      # every 5 minutes
  workflow_dispatch:                        # manual re-run
concurrency:
  group: runner-poll
  cancel-in-progress: false                 # prevent overlapping polls
jobs:
  poll:
    runs-on: ubuntu-latest
    timeout-minutes: 4                      # hard bound < cron interval
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install -e .
      - name: Restore ephemeral health cache
        uses: actions/cache@v4
        with:
          path: .runner-state/
          key: runner-state-${{ github.run_id }}
          restore-keys: runner-state-
      - run: python -m runner poll
        env:
          JIRA_BASE_URL: ${{ secrets.JIRA_BASE_URL }}
          JIRA_EMAIL:    ${{ secrets.JIRA_EMAIL }}
          JIRA_TOKEN:    ${{ secrets.JIRA_TOKEN }}
          GH_TOKEN:      ${{ secrets.GITHUB_TOKEN }}   # for gh issue create (§6.4)
      - name: Save ephemeral health cache
        if: always()
        uses: actions/cache/save@v4
        with:
          path: .runner-state/
          key: runner-state-${{ github.run_id }}
```

**Run logic (`python -m runner poll`):**

1. Read watermark from the Jira System Config issue (§3.4).
2. Fetch changelog entries with `id > watermark` across the project (batched via JQL `updated > <last_poll_timestamp>`, then per-issue `/changelog` for the exact IDs).
3. For each changelog entry in ascending `id` order:
   a. Classify as `issue_created`, `subtask_transitioned_to_done`, or `ignored`.
   b. Dispatch to `rule1_unit_created` or `rule2_subtask_done` accordingly.
   c. Update local `max_changelog_id` tracker.
4. On clean completion: write `max_changelog_id` and `Last Successful Poll At` to the System Config issue.
5. On any fatal error: **do not advance the watermark** — the next poll re-processes the batch, and idempotency labels (§5.3) ensure duplicates are detected and skipped.

**Rule 1 (T1 on Unit created) body** — mirrors [`JiraImplementation.md §4.1`](./JiraImplementation.md), with a **`Difficulty` fallback** so that Units missing the `Difficulty` field at creation time still receive a Learn Subtask (preventing silent guard-failures that previously stranded Units without a first work-artefact):

```python
def rule1_unit_created(event: ChangelogEvent) -> None:
    if not (event.is_new_issue and event.issuetype in UNIT_ISSUE_TYPES):
        return
    unit = jira_client.get_issue(event.issue_key)
    if unit.fields["Stage"] is None:
        return   # Stage remains a hard pre-state — no sensible default exists
    # Difficulty fallback: if the user omits Difficulty at creation, seed
    # RevisionTarget to the Easy threshold (config.RevisionTargetDefault = 2)
    # rather than aborting. Aligns with JiraImplementation.md §2 which
    # specifies RevisionTarget is seeded from Difficulty at T1.
    difficulty = unit.fields["Difficulty"]
    rev_target = (
        config.RevisionTarget[difficulty] if difficulty in config.RevisionTarget
        else config.RevisionTargetDefault   # = 2 (Easy workload threshold)
    )
    key = idempotency.compute_key(unit.key, event.id, "T1")
    if idempotency.has_been_applied(unit, key):
        return
    jira_client.create_subtask(
        parent=unit.key,
        summary=f"[{unit.stage}][Learn] — {unit.summary}",
        labels=["learn", f"idem:{key}"],
        story_points=2,
    )
    jira_client.update_issue(unit.key, fields={"Revision Target": rev_target})
    audit.post(unit.key, transition="T1",
               note=None if difficulty else "Difficulty missing; defaulted RevisionTarget=2")
```

**Audit-comment addendum on fallback.** When the fallback fires, the Layer 2 audit comment (§5.2) appends a single line `Note: Difficulty missing at creation; RevisionTarget defaulted to 2 (Easy).` This makes the defaulting visible to the user at read-time, enabling them to set `Difficulty` later and manually bump `RevisionTarget` if the Unit is actually Medium or Hard. No corresponding T1 re-fire is needed — `RevisionTarget` is a user-editable number and the state machine reads it fresh on every subsequent Rule 2 dispatch.

**Rule 2 (Subtask → Done dispatch)** — mirrors [`JiraImplementation.md §4.2`](./JiraImplementation.md) with Regress-first evaluation from [`LivingRequirements.md §5.5`](./LivingRequirements.md):

```python
def rule2_subtask_done(event: ChangelogEvent) -> None:
    if not (event.is_status_change_to_done and event.issuetype == "Sub-task"):
        return
    subtask = jira_client.get_issue(event.issue_key)
    unit = jira_client.get_issue(subtask.parent_key)
    transition = state_machine.dispatch(
        stage=unit.stage, work_type=unit.work_type, lifecycle=unit.lifecycle,
        rev_done=unit.rev_done, rev_target=unit.rev_target,
        outcome=subtask.outcome,
    )
    key = idempotency.compute_key(unit.key, event.id, transition)
    if idempotency.has_been_applied(unit, key):
        return
    handler = {"T2": _t2, "T3": _t3, "T4": _t4, "T12": _t12, "T13": _t13, "NOOP": _noop}[transition]
    handler(unit, subtask, key)
    audit.post(unit.key, transition=transition, ...)
```

### 4.2 `stale-scan.yml` — Rule 4 weekly scan (T9)

**Workflow:**

```yaml
name: runner-stale-scan
on:
  schedule: [{ cron: '0 10 * * MON' }]      # Monday 10:00 UTC
  workflow_dispatch:
jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install -e .
      - run: python -m runner stale
        env:
          JIRA_BASE_URL: ${{ secrets.JIRA_BASE_URL }}
          JIRA_EMAIL:    ${{ secrets.JIRA_EMAIL }}
          JIRA_TOKEN:    ${{ secrets.JIRA_TOKEN }}
          GH_TOKEN:      ${{ secrets.GITHUB_TOKEN }}
```

**Run logic (`python -m runner stale`):**

1. Execute the `IP-Stale-Eligible` JQL from [`JiraImplementation.md §9.2`](./JiraImplementation.md) (Solo profile) — uses the durable `Has Had Test` flag, not `issueFunction`.
2. For each matched Unit:
   a. Create a `Test` Subtask (`[Stage][Test] — <summary>`, `labels=[test]`, 2 SP, due +2 business days).
   b. **Set `Has Had Test := true`** on the Unit (T9 lifetime-idempotency guard).
   c. Post audit comment `[Runner][T9] Stale scan — Test Subtask created`.
3. Update `Last Stale Scan At` on the System Config issue.

Rule 4 **never writes tuple fields** (`LastWorkedAt`, `LastTransitionedAt`, `RevisionDone`, `WorkType`, `Stage`, `Lifecycle`) — preserving the invariant in [`LivingRequirements.md §5.2`](./LivingRequirements.md) T9.

### 4.3 Rule 3 (Lifecycle change, T5–T8) remains in Jira Automation

As established in the Capacity Analysis, the three §9.1 Manual-Trigger buttons are the natural writers of the `Lifecycle` field. A Lifecycle field-change trigger inside Jira Automation then fires T5/T6/T7/T8 — ~15 runs/month, well under the 100-run cap. **The External Runner does not touch Rule 3.** This split is recorded here because a naïve "move everything to GitHub" interpretation would lose the one-click UX.

---

## 5. Logging & Traceability

Two log substrates run in parallel — one for the engineer debugging the runner, one for the user reading the Unit's history.

### 5.1 Layer 1 — GitHub Actions run logs (technical debugging)

**Audience:** the developer debugging a specific run.
**Retention:** 90 days on public repos (free tier), sufficient for post-hoc analysis.
**Format:** one JSON object per significant step, emitted to stdout. Every line carries the full traceability quadruple `(run_id, event_id, unit_key, transition_id)`.

```json
{"ts":"2026-04-18T10:03:42Z","lvl":"INFO","run_id":"7241","event_id":"12345678",
 "unit":"CORE-142","subtask":"CORE-143","transition":"T2","msg":"dispatch_ok",
 "pre":{"stage":"Intermediate","work_type":"Learn","lifecycle":"Active","rev_done":0},
 "post":{"stage":"Intermediate","work_type":"Revise","lifecycle":"Active","rev_done":0},
 "new_subtask":"CORE-144","due":"2026-04-22","idem_key":"8c4d2a1f9bb7"}
```

**Logger design (`logging_ext.py`):**

```python
import json, logging, os, sys

class StructuredFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts":    self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "lvl":   record.levelname,
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "msg":   record.getMessage(),
        }
        # Context vars propagated via logging.LoggerAdapter
        for k in ("event_id", "unit", "subtask", "transition", "idem_key"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload, default=str)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
```

### 5.2 Layer 2 — Jira audit comment (Unit Audit Trail)

**Audience:** the user scanning the Unit's timeline to understand its history.
**Retention:** permanent — comments are durable Jira artefacts.
**Emission rule:** one comment per executed transition, posted on the **parent Unit** (not the Subtask), so a single page shows the whole lifecycle.

**Canonical format:**

```
[Runner][T2] Learn#1 → Revise#1
  RevisionDone: 0 → 0 (target 3)
  Outcome: Pass
  DueDate(Revise#1): 2026-04-22  (RevisionGap[1] = 2bd)
  run: 7241 · event: 12345678 · key: idem_8c4d2a1f9bb7
```

**Field rationale:**

| Field | Purpose |
|---|---|
| `[Runner][T2]` | Transition ID indexes into [`LivingRequirements.md §5.2`](./LivingRequirements.md) |
| Pre/post tuple line | Makes state motion visible without requiring the tuple-inspection view ([`LivingRequirements.md §11`](./LivingRequirements.md) FR11) |
| Outcome | Regress path auditable — T12/T13 firings leave a permanent trail |
| Due date + gap | User can verify `RevisionGap` arithmetic without cross-referencing the spec |
| `run` / `event` / `key` | Join columns to Layer 1 logs and the idempotency mechanism (§5.3) |

**Logging matrix — what goes where:**

| Event class | Layer 1 (GH logs) | Layer 2 (Jira comment) |
|---|---|---|
| Successful transition (T1–T13) | ✅ `lvl: INFO` | ✅ always |
| No-op dispatch (Test Pass, already-applied replay) | ✅ `lvl: DEBUG` | ❌ (would be noise) |
| Non-fatal error (Subtask created, comment failed) | ✅ `lvl: WARN` | ✅ best-effort retry; if still fails, log only |
| Fatal error (Jira 5xx, transaction aborted) | ✅ `lvl: ERROR` | ✅ on parent Unit: `[Runner][ERROR] T2 aborted: <reason>; will retry next run` |
| Dead-man's-switch triggered | ✅ `lvl: ERROR` | ❌ (alert opens as GitHub Issue — see §6.4) |

### 5.3 Idempotency — preventing duplicate side-effects on retry

**Trace chain:** `GitHub run_id ← Jira changelog.id ← unit_key ← transition_id ← idempotency_key`. All five appear in every Layer 1 log line and Layer 2 audit comment. Given any failure symptom, the developer can start from either end and reach the other.

**Key computation:**

```python
import hashlib

def compute_key(unit_key: str, event_id: str, transition_id: str) -> str:
    raw = f"{unit_key}|{event_id}|{transition_id}"
    return "idem_" + hashlib.sha256(raw.encode()).hexdigest()[:12]
```

Properties:
- **Deterministic** — replaying the same Jira event produces the same key.
- **Scoped per transition** — a single Jira event can legitimately produce multiple transitions; each gets its own key.
- **Collision-resistant** — 48 bits of entropy is overkill for the ~10³ events/year this system produces.

**Where the key lives:** written as a Jira **label** on the Subtask created by the transition:

```
Subtask.Labels += [f"idem:{key}"]   # e.g. "idem:8c4d2a1f9bb7"
```

**Check-then-act pattern** — every side-effecting rule begins with:

```python
def has_been_applied(unit: Unit, key: str) -> bool:
    jql = f'parent = "{unit.key}" AND labels = "idem:{key}"'
    return jira_client.search(jql).total > 0
```

If the query returns a hit, the transition is a replay — skip side-effects, re-post the audit comment only if content-matching fails, exit.

**Why Jira label and not GH Cache / external KV:**

1. The authoritative "did this transition happen?" record lives next to the artefact it produced.
2. No cross-system clock skew — the check and the write are both against Jira.
3. A user manually deleting an accidentally-created Subtask also deletes the label, correctly unblocking a re-apply.

**T9 idempotency is separate:** Rule 4 uses the durable `Has Had Test` Boolean flag (lifetime scope), not `idem:*` labels (per-event scope) — see [`JiraImplementation.md §9.2`](./JiraImplementation.md).

---

## 6. Reliability & Alerting

### 6.1 Error classification (acted on at the `jira_client.py` boundary)

| Class | HTTP | Handling | Alert threshold (§6.3) |
|---|---|---|---|
| Transient network | — (OSError, timeout) | `tenacity` retry: 3 attempts, exp back-off 1s/2s/4s | Counts towards 5xx if all retries fail |
| Rate limit | 429 | Respect `Retry-After` header; up to 5 retries with back-off | 5 consecutive post-retry failures → alert |
| Auth | 401, 403 | No retry — token is revoked or scope-reduced | **Immediate alert (threshold 1)** |
| Not found | 404 | No retry — Unit/Subtask deleted mid-flight; log WARN, skip | Not alerted; user-initiated deletion is legitimate |
| Conflict | 409 | Reload entity, re-evaluate pre-state, retry once | Alert if still conflicting |
| Server error | 500, 502, 503, 504 | `tenacity` retry: 3 attempts, exp back-off | 3 consecutive post-retry → alert |
| Logic bug | — (unhandled exception in `state_machine.py` or `rules.py`) | No retry; full stack trace to Layer 1 | **Immediate alert (threshold 1)** |

### 6.2 Retry policy (`tenacity`)

The `tenacity` library was chosen over alternatives (`retrying`, `backoff`, `urllib3` native retries) because its decorator-based composition keeps retry policy on the call site, visually separated from business logic, and its `retry_if_exception_type` predicate composes cleanly with custom exception classes:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, wait_random, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8) + wait_random(0, 1),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError,
                                    RateLimitError, ServerError)),
    reraise=True,
)
def _request(method: str, path: str, **kwargs) -> httpx.Response:
    response = _client.request(method, path, **kwargs)
    if response.status_code == 429:
        raise RateLimitError(retry_after=response.headers.get("Retry-After"))
    if 500 <= response.status_code < 600:
        raise ServerError(response.status_code)
    if response.status_code in (401, 403):
        raise AuthError(response.status_code)
    response.raise_for_status()
    return response
```

Worst-case latency per call is ~15 s (three attempts, 8 s max back-off), well under the 4-minute job timeout.

### 6.3 Dead-Man's-Switch — consecutive-failure tracking

**Persistent state:** a small JSON document in GH Actions Cache under key `runner-health-state`, with a pointer to the currently-open alert mirrored into the Jira System Config issue (`Open Alert Issue Url` field, §3.2) for durability across cache eviction.

```json
{
  "consecutive_failures": 2,
  "recovery_streak": 0,
  "last_success_at": "2026-04-17T10:03:42Z",
  "last_failure_at": "2026-04-18T10:08:11Z",
  "last_failure_kind": "http_401",
  "open_alert_issue": null
}
```

**Counter logic** (wraps every runner entrypoint — `poll`, `stale`, `health`):

```python
def with_health_tracking(run_rule: Callable[[], None]) -> None:
    state = health.load_state()
    try:
        run_rule()
        state["consecutive_failures"] = 0
        state["last_success_at"] = now_iso()
        health.maybe_close_alert(state)          # §6.4
    except FatalError as e:
        kind = health.classify(e)                # http_401 | http_429 | http_5xx | logic
        state["consecutive_failures"] += 1
        state["last_failure_at"] = now_iso()
        state["last_failure_kind"] = kind
        state["recovery_streak"] = 0
        threshold = config.THRESHOLDS[kind]
        if state["consecutive_failures"] >= threshold and state["open_alert_issue"] is None:
            state["open_alert_issue"] = health.open_alert(state, e)
        raise
    finally:
        health.save_state(state)
```

**Thresholds** (tunable in `config.py`):

| Kind | Threshold | Rationale |
|---|---|---|
| `http_401` | **1** | Token revoked or expired — always user-actionable, always urgent |
| `http_429` | 5 | Rate-limit bursts are transient; don't spam on them |
| `http_5xx` | 3 | Atlassian outage typically recovers within 15 min; 3 consecutive (~15 min) warrants signal |
| `logic` | **1** | Unhandled exception is always a bug; stop-the-line |

### 6.4 Alert creation (GitHub CLI)

The `open_alert` function shells out to `gh` — available by default on GitHub-hosted runners:

```python
def open_alert(state: dict, error: Exception) -> str:
    kind = state["last_failure_kind"]
    count = state["consecutive_failures"]
    body = format_alert_body(state, error)
    result = subprocess.run(
        ["gh", "issue", "create",
         "--title", f"Runner System Alert: {kind} ({count} consecutive)",
         "--label", "system-alert,runner",
         "--body", body],
        capture_output=True, text=True, check=True,
    )
    issue_url = result.stdout.strip()
    # Mirror to Jira System Config for durability across cache eviction
    watermark.write_field("Open Alert Issue Url", issue_url)
    return issue_url
```

**Alert body template:**

```markdown
## Failure kind
{{ kind }}

## Consecutive failures
{{ count }}  (threshold: {{ threshold }})

## Last success
{{ last_success_at }}  ({{ age_hours }}h ago)

## Last failure
- **When:** {{ last_failure_at }}
- **Run:** {{ github_server_url }}/{{ repo }}/actions/runs/{{ run_id }}
- **Symptom:** {{ error_message }}

## Suggested action
{% if kind == "http_401" %}
Rotate `JIRA_TOKEN` secret. Atlassian → Account Settings → Security → API tokens → Revoke & regenerate. Update the GitHub Secret in *Repository Settings → Secrets and variables → Actions*.
{% elif kind == "http_429" %}
Back-pressure on Jira. Raise poll interval in `poll-dispatch.yml` or reduce batch size in `runner/config.py`.
{% elif kind == "logic" %}
Unhandled exception in state machine. See run logs; add a regression test to `tests/test_state_machine.py` and file a fix PR before re-enabling the cron.
{% endif %}

## Auto-close
This issue auto-closes after 3 consecutive successful runs.
```

**Alert lifecycle — auto-close on recovery:**

```python
def maybe_close_alert(state: dict) -> None:
    if state["open_alert_issue"] is None:
        return
    state["recovery_streak"] = state.get("recovery_streak", 0) + 1
    if state["recovery_streak"] >= 3:
        subprocess.run([
            "gh", "issue", "comment", state["open_alert_issue"],
            "--body", f"Auto-closing: 3 consecutive successful runs since {state['last_failure_at']}.",
        ], check=True)
        subprocess.run(["gh", "issue", "close", state["open_alert_issue"]], check=True)
        state["open_alert_issue"] = None
        state["recovery_streak"] = 0
        watermark.write_field("Open Alert Issue Url", None)
```

### 6.5 Independent healthcheck workflow

The health-tracking logic above detects failures that happen during a run. It cannot detect the case where the cron itself has stopped firing (GitHub outage, workflow disabled, billing lapse on a later private repo). For that, a separate workflow runs every 6 hours and checks `Last Successful Poll At` in the System Config issue:

```yaml
name: runner-healthcheck
on:
  schedule: [{ cron: '0 */6 * * *' }]
  workflow_dispatch:
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install -e .
      - run: python -m runner health
        env:
          JIRA_BASE_URL: ${{ secrets.JIRA_BASE_URL }}
          JIRA_EMAIL:    ${{ secrets.JIRA_EMAIL }}
          JIRA_TOKEN:    ${{ secrets.JIRA_TOKEN }}
          GH_TOKEN:      ${{ secrets.GITHUB_TOKEN }}
```

The healthcheck has its own trivial alert path — it cannot rely on the main runner's state machine, so it calls `gh issue create` directly if `now() - Last Successful Poll At > 30 min`.

### 6.6 Partial-success resolution (2-phase-commit light)

A transition has up to four side-effects, executed in this order:

1. **Create Subtask** (with `idem:<key>` label pre-attached).
2. **Edit Parent Unit fields** (`WorkType`, `RevisionDone`, `Lifecycle`, timestamps).
3. **Edit `Has Had Test` flag** — T9 only.
4. **Post audit comment** on Parent.

**Failure between steps produces a partial state. Recovery:**

| Failure between | State after failure | Next run detects via | Recovery action |
|---|---|---|---|
| (before 1) | no Subtask | idempotency label absent | Retry from step 1 |
| 1 → 2 | Subtask exists; Parent fields stale | label present **AND** Parent tuple ≠ expected post-state | Resume at step 2 (writes are idempotent — set fields to target values) |
| 2 → 3 (T9 only) | Subtask + fields OK; flag unset | Parent tuple = expected **AND** `Has Had Test` still `false` | Resume at step 3 (idempotent set) |
| 3 → 4 | All state correct; comment missing | State converged **AND** no comment carries `idem:<key>` suffix | Re-post audit comment |

The invariant: **every write is either idempotent (field set to specific value) or guarded by a label check (Subtask creation).** No compensating-transaction / rollback is ever required — the runner only moves forward.

### 6.7 Poisoned-event quarantine

If the same event ID fails 3 consecutive runs at the same transition, it goes to the `runner-poisoned-events` cache list and the poll moves past it. The daily healthcheck posts a `[Runner][QUARANTINE]` audit comment on the affected Unit and opens a repo Issue with the raw payload, so the user can triage without the runner continuing to crash on the same event forever.

---

## 7. Future Scope

### 7.1 Cloudflare Worker Relay (deferred)

The 5-minute polling strategy in §4.1 has a 0 – 300 s event-ingest latency. For the described workload (a dozen Done-events per day spread over the day), this is indistinguishable from instant. **The relay becomes worthwhile only if heavy-review sessions reveal the latency as genuinely annoying** — e.g., a session where the user rapidly clears a backlog of Revise Subtasks and wants the next layer spawned immediately. The relay is documented here for optionality; it is not part of the current v0.1.0 implementation.

#### 7.1.1 Feasibility summary

| Free-tier limit (Cloudflare Workers) | Value | Headroom for Runner |
|---|---|---|
| Requests per day | 100,000 | Workload ~16 events/day → **6,250× headroom** |
| CPU time per request | 10 ms | Relay logic ~1 ms |
| Burst rate | 1,000 req/min | N/A for single-user workload |
| Worker secrets | Up to 128 bindings | Need 2: `GITHUB_PAT`, `JIRA_WEBHOOK_SECRET` |
| Custom subdomain | `<name>.workers.dev` (free HTTPS) | Sufficient — no domain purchase |
| Credit card required | No | ✓ zero-cost goal preserved |
| Cold-start latency | ~0 ms (V8 isolates) | ✓ no warm-up |

End-to-end latency changes from 0 – 300 s (polling) to 1 – 3 s (relay).

#### 7.1.2 Setup time estimate (solo developer)

| Step | Time |
|---|---|
| Cloudflare account creation (no card) | 5 min |
| `wrangler` CLI install + `wrangler login` | 5 min |
| Write Worker (~40 LOC — see §7.1.3) | 20 min |
| `wrangler deploy` + capture `*.workers.dev` URL | 5 min |
| Store `GITHUB_PAT` and `JIRA_WEBHOOK_SECRET` as Worker Secrets | 5 min |
| Configure Jira outgoing webhook → Worker URL with secret in path | 10 min |
| End-to-end test | 10 min |
| **Total** | **~60 min** |

#### 7.1.3 Worker implementation sketch

```javascript
export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }
    const url = new URL(request.url);
    // Secret in path prevents random callers hitting the /dispatches relay
    if (url.pathname !== `/jira/${env.JIRA_WEBHOOK_SECRET}`) {
      return new Response("Not Found", { status: 404 });
    }
    const payload = await request.json();
    const ghResponse = await fetch(
      `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_PAT}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "runner-relay/1.0",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          event_type: "jira-event",
          client_payload: {
            webhookEvent: payload.webhookEvent,
            issueKey: payload.issue?.key,
            changelogId: payload.changelog?.id,
            timestamp: payload.timestamp,
          },
        }),
      }
    );
    return new Response(ghResponse.ok ? "ok" : "relay_error",
                        { status: ghResponse.status });
  },
};
```

The corresponding GitHub Actions workflow would add a `repository_dispatch` trigger to `poll-dispatch.yml`:

```yaml
on:
  schedule: [{ cron: '*/5 * * * *' }]      # poll remains as fallback
  repository_dispatch:                      # NEW — relay-driven near-instant path
    types: [jira-event]
  workflow_dispatch:
```

#### 7.1.4 Trade-off summary

| Dimension | Cron-poll (current) | CF Worker relay (future) |
|---|---|---|
| End-to-end latency | 0 – 300 s | 1 – 3 s |
| Moving parts | 1 (GH Actions) | 3 (GH Actions + Worker + Jira webhook config) |
| Secret locations | 1 (GitHub Secrets) | 2 (GitHub Secrets + Worker Secrets) |
| Points of silent failure | 1 | 3 |
| Recovery after outage | Automatic next cron tick | Automatic only if Worker healthy; else user intervention |
| Setup time | 0 min | +60 min |

**Adoption trigger:** the relay should be added **only if** the user logs a real friction case where a 5-minute wait on a heavy-review session materially disrupted flow. Until then, the fewer-moving-parts path wins on Principle 3.

### 7.2 Other deferred items

- **Multi-project support in a single runner deployment.** Currently each project (`Core`, `Extended`, `Independent-*`) gets its own System Config issue; the runner can be extended to iterate all projects in one workflow run. Deferred until the solo user has more than one active project.
- **Structured progress-velocity analytics** ([`LivingRequirements.md §6 FR11`](./LivingRequirements.md)). The audit-comment stream (§5.2) already contains the raw data; a downstream analytics script that tallies transitions per week and posts a weekly summary is a one-file addition. Deferred until baseline stability is proven.
- **Bridge-problem generation** (rejected in [`LivingRequirements.md §11`](./LivingRequirements.md) v0.7.6 changelog). Explicitly out of scope for the runner — content generation lives outside the execution tracker.

---

## 8. Repository Privacy & Security Posture

### 8.1 Decision — Public repository (formalised)

The `jira-stateless-runner` repository **MUST be public**. This is a locked architectural decision, not a recommendation. Rationale:

| Factor | Public (free) | Private (free) |
|---|---|---|
| Actions minutes/month | **Unlimited** | 2,000 cap |
| Runner projected burn at `*/5 * * * *` | ~8,760 min/month | ~4.4× over cap |
| Cron-interval compromise required | None | Would force `*/15` or longer, degrading the §9.1 Manual-Trigger UX |
| Secret masking in logs | ✅ Automatic | ✅ Automatic |
| Fork-PR secret leakage | ✅ Mitigated (see §8.3) | N/A (no public forks) |

Private-repo operation is **not supported** under Posture J-C v0.1.x because the free-tier minutes cap is incompatible with the 5-minute cron that the Solo-User Profile ([`JiraImplementation.md §9`](./JiraImplementation.md)) assumes for timely Rule 1 / Rule 2 dispatch. A user with strict policy against public code must either (a) accept upstream Jira latency by dropping to `*/15` or longer, (b) self-host the runner, or (c) pay for a minutes upgrade — none of which are in scope for v0.1.x.

### 8.2 Credential-handling mandate

All Jira credentials **MUST** be stored exclusively in GitHub Secrets at repo scope. The following constraints are normative for this specification:

| Secret | Storage | Never permitted |
|---|---|---|
| `JIRA_BASE_URL` | GitHub Secrets only | Committed to the repo; hard-coded in YAML; echoed in log lines |
| `JIRA_EMAIL` | GitHub Secrets only | Committed; hard-coded; included in audit comments |
| `JIRA_TOKEN` | GitHub Secrets only | Committed in any form (encrypted or not); printed to stdout; written to artefacts; included in alert bodies |
| `GITHUB_TOKEN` | Auto-provided by GitHub Actions | N/A (ephemeral per-run) |

**Enforcement:**

1. **Pre-commit gate.** A repo-level `.gitleaks.toml` is mandatory; the `pre-commit` hook runs `gitleaks detect --redact` before every commit. The hook is bootstrap-installed by `make setup`.
2. **CI scan.** `poll-dispatch.yml` and `stale-scan.yml` both run a `gitleaks` step before `pip install -e .` — any hit fails the workflow before it can reach Jira.
3. **Branch protection.** `main` must require PR reviews; force-push disabled. This prevents a compromised write token from rewriting workflow YAMLs to exfiltrate secrets via an ad-hoc `echo $JIRA_TOKEN`.
4. **Token scope.** `JIRA_TOKEN` is an Atlassian API token scoped to the user's Jira account. Atlassian does not offer per-project scoping; treat the token as high-value. Rotate quarterly via the process documented in the §6.4 alert body for `http_401`.
5. **Fork-PR policy.** Repo → Settings → Actions → "Fork pull request workflows" → **"Require approval for all outside collaborators"**. GitHub's default is that `pull_request` events from forks do not receive secrets, but explicit opt-in approval for fork-PR CI runs is required to make an accidental permission-elevation impossible.
6. **`pull_request_target` is BANNED.** Use of `pull_request_target` is forbidden in every workflow under `.github/workflows/` because it exposes secrets to untrusted fork code. The `poll-dispatch.yml`, `stale-scan.yml`, and `healthcheck.yml` workflows are all cron/dispatch-triggered and have no legitimate need for it.

### 8.3 Log-content mandate — `logging_ext.py` must exclude user-content

Because the repository is public and run logs are world-readable for 90 days, the structured logger (§5.1) **MUST NOT** emit issue `summary` text, `description` text, or `comment` bodies at INFO level. Interview-prep topics in a Unit summary may reveal personally or commercially sensitive context (active job-search signal, target-company-specific study patterns, current-employer confidentiality boundaries) that the user has not consented to publish.

**Fields that MAY appear in INFO-level Layer 1 logs:**

| Allowed | Rationale |
|---|---|
| `unit_key` (e.g., `CORE-142`) | Opaque identifier; reveals only project scale |
| `subtask_key` | Same |
| `transition_id` (T1–T13) | State-machine taxonomy; not user-content |
| `idem_key` (12-hex digits of SHA-256) | Deterministic hash; not reversible to inputs |
| Tuple field values (`Stage`, `WorkType`, `Lifecycle`, `RevisionDone`, `RevisionTarget`, `Outcome`) | Enumerated state; fixed vocabulary |
| `event_id`, `run_id`, `ts` | Opaque identifiers |

**Fields that MUST NOT appear in INFO-level logs:**

| Forbidden at INFO | Rationale | Where it may appear |
|---|---|---|
| Issue `summary` text | User-authored content; topic-revealing | DEBUG only, local runs only |
| Issue `description` / `comment` bodies | Same | DEBUG only, local runs only |
| `Difficulty` (if set) | User-classification signal; low risk but unnecessary | Omit by default |
| Any custom-field value not in the allow-list above | Default-deny | Add to allow-list explicitly when justified |

**`logging_ext.py` implementation constraints:**

```python
# Allow-list-driven — adding a field here is a deliberate act:
_ALLOWED_INFO_FIELDS = frozenset({
    "ts", "lvl", "run_id", "event_id", "unit", "subtask",
    "transition", "idem_key", "stage", "work_type", "lifecycle",
    "rev_done", "rev_target", "outcome", "due", "msg",
})

class StructuredFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno <= logging.INFO:
            # Drop any field not explicitly allow-listed
            payload = {k: v for k, v in self._collect(record).items()
                       if k in _ALLOWED_INFO_FIELDS}
        else:
            payload = self._collect(record)   # DEBUG / WARN / ERROR retain full context
        return json.dumps(payload, default=str)
```

The allow-list is the authoritative policy; any attempt to log disallowed content via `logger.info("summary: %s", unit.summary)` silently drops the field on the way out. A `tests/test_logging.py` regression test asserts that `summary` and `description` are filtered out of INFO payloads on representative records.

### 8.4 Remaining exposure (accepted)

Even with the mandates above, a public repo exposes:

| Exposure | Severity | Accepted because |
|---|---|---|
| `JIRA_BASE_URL` visible in workflow YAML (e.g. `env: JIRA_BASE_URL: ${{ secrets... }}` reveals the env var name, not the value) | None | The secret value is masked; the variable name is not sensitive |
| Audit-comment URL pattern (`$BASE/browse/CORE-142`) appears in alert bodies | Low | URL shape is public Jira convention; access still requires auth |
| Python source of the state machine | Low | Code is the contract; exposing it aids future maintainers and has no strategic value to withhold |
| Issue key patterns (`CORE-*`) | Low | Project scale is inferrable from key range; mitigation would require non-standard numbering |
| Count and timing of cron runs | None | Workflow schedule is in the YAML; no additional signal |

None of these meet the threshold for private-repo operation given the §8.1 cost trade-off.

---

## 9. Implementation Roadmap

### 9.1 Test-first milestone ordering

The **first development milestone MUST be the parametric test suite in `tests/test_state_machine.py`**, validating all 23 rows (D1–D23) of [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md), **before** any side-effecting handler in `rules.py` is implemented. This ordering is normative for this specification.

**Rationale:**

1. **Pure-function testability.** `state_machine.dispatch()` (§2.3) takes a state tuple and `Outcome`, returns a `TransitionID` — no I/O, no time, no external calls. The matrix is a complete decision table over the input space. Test-first lets the spec↔code contract be locked down against a single artefact (`ImplementationTestMatrix.md §3`).
2. **Cheapest defect discovery.** A dispatch bug found in `tests/test_state_machine.py` costs one assertion-rewrite. The same bug found in production costs: a poisoned event → a dead-man's-switch alert → manual quarantine → audit-comment triage → replay with a `RevisionDone` rollback — the asymmetry is ~100× in effort.
3. **Matrix lock-in.** The 23 rows are the tightest spec-to-code contract in the architecture. Building handlers first would allow the state machine to drift from the matrix unnoticed.
4. **Enables rule-handler confidence.** With the dispatch proven, `rules.py` becomes a thin adapter: "given transition T, perform these 4 writes." The reviewer's attention collapses onto the side-effect sequence (§6.6), not the dispatch semantics.

### 9.2 Milestone sequence

| # | Milestone | Deliverable | Gate to next |
|---|---|---|---|
| **M0** | Scaffold | `pyproject.toml`, `runner/__init__.py`, empty module stubs matching §2.1 tree | `pip install -e '.[dev]'` succeeds; `pytest` collects zero tests without error |
| **M1** | **Parametric dispatch tests** | `tests/test_state_machine.py` with **23 parametrised cases** covering D1–D23 verbatim from [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md); all currently RED against a `NotImplementedError` stub | All 23 cases defined; each names its matrix row (`D1`…`D23`) |
| **M2** | **`state_machine.py` implementation** | Implement `dispatch()` per §2.3 until all 23 M1 tests pass. **No other module is touched.** | 23/23 GREEN; no regressions; `ruff` clean |
| **M3** | `models.py` + `jira_client.py` | Pydantic types for `Unit`, `Subtask`, `ChangelogEvent`; `httpx`-based client with `tenacity` retry per §6.2. Mocked-HTTP tests in `tests/test_jira_client.py`. | Transient-error, 429, 401 branches all covered |
| **M4** | `idempotency.py` + `audit.py` | Key computation, label-based check-then-act, canonical comment formatter. Tests in `tests/test_idempotency.py`. | Replay-safety asserted in tests |
| **M5** | `rules.py` — Rule 1 | `rule1_unit_created` per §4.1, including **Difficulty fallback** (§4.1 updated pseudocode). Integration test against a mock Jira. | Difficulty-present and Difficulty-missing paths both create Learn Subtask |
| **M6** | `rules.py` — Rule 2 | `rule2_subtask_done` per §4.1, Regress-first per §5.5. Dispatch table exercised end-to-end. | All T2/T3/T4/T12/T13 branches covered by integration tests |
| **M7** | `rules.py` — Rule 4 | `rule4_stale_scan` per §4.2, Solo-profile `Has Had Test` durability ([`JiraImplementation.md §9.2`](./JiraImplementation.md)). | Lifetime idempotency asserted; no re-fire after `Has Had Test = true` |
| **M8** | `watermark.py` + System Config bootstrap | Jira System Config read/write per §3. `BootstrapIncompleteError` self-check per §3.3. | Filter self-check blocks the runner when §3.3 bootstrap is incomplete |
| **M9** | `health.py` + `logging_ext.py` | Dead-man's-switch per §6.3–§6.4; allow-listed INFO logger per §8.3. `tests/test_logging.py` asserts `summary` filtering. | Alert open/close cycle works end-to-end in a mocked-`gh` integration test |
| **M10** | Workflow YAMLs | `poll-dispatch.yml`, `stale-scan.yml`, `healthcheck.yml` per §4 and §6.5 | Dry-run on a throwaway Jira project succeeds |
| **M11** | **First live `workflow_dispatch`** | Manual trigger against the user's Jira project | `Last Successful Poll At` advances; audit comment posted on a test Unit |
| **M12** | Cron enablement | Remove `workflow_dispatch`-only guard; allow `schedule` to fire | 3 consecutive green cron runs observed |

### 9.3 Out-of-order work is forbidden

Skipping M1→M2 (test-first dispatch) to "get a live demo working" is the single most common failure mode for this class of system and is **explicitly prohibited** by this roadmap. The matrix is cheap to encode (23 tuples); the cost of discovering a dispatch bug at M11 is not. If schedule pressure tempts an M3 or M5 start before M2 GREEN, the correct response is to narrow the M1 scope (e.g., D1–D9 only for an MVP interview-prep project) rather than to skip it.

### 9.4 Definition of Done per milestone

Every milestone's PR MUST:

1. Land with unit tests exercising the new module (not only integration tests).
2. Pass `ruff check` with the repo's `pyproject.toml` lint config.
3. Update `ExternalRunner.md` §2.1 or the relevant subsection if the module contract changes.
4. Include a CHANGELOG line in the module's docstring or a top-level `CHANGELOG.md`.
5. Pass the `gitleaks` pre-commit gate (§8.2).

### 9.5 Alignment with Solo-User Profile

The roadmap preserves the Solo-User Profile invariants from [`JiraImplementation.md §9`](./JiraImplementation.md):

- **Rule 3 stays in Jira Automation** (§4.3) — the three Manual-Trigger buttons (Archive / Pause / Resume) are not reimplemented on the GitHub side, so the ~15 runs/month of Rule 3 remain under the 100-run cap.
- **`Has Had Test` durable flag** is the T9 idempotency guard at M7 (not a new `idem:*` label), matching [`JiraImplementation.md §9.2`](./JiraImplementation.md) exactly.
- **Difficulty fallback** (M5) aligns with [`JiraImplementation.md §2`](./JiraImplementation.md)'s "seeded from Difficulty at T1" rule by preserving seeding when Difficulty is present, and providing a conservative Easy-threshold default when it is absent, rather than failing the T1 pre-state guard.
- **§3.3 filter bootstrap self-check** is enforced at M8 to keep the user-facing Working-Set / Stale / Velocity views clean of the System Config issue — preserving the low-cognitive-load promise of §9.

---

## 10. Changelog

- **v0.1.1 (2026-04-18)** — Operational-refinement pass following the v0.1.0 operational-guide review. Five changes, none altering the normative state machine or [`LivingRequirements.md`](./LivingRequirements.md): (i) §2.2 `config.py` and §4.1 Rule 1 now specify a **Difficulty fallback** — missing `Difficulty` at Unit creation defaults `RevisionTarget = 2` (Easy workload threshold) via `config.RevisionTargetDefault`, replacing the previous silent guard-failure; the audit comment notes the defaulting for user visibility. (ii) §3.3 filter updates are **promoted from recommendation to mandatory bootstrap step**, enforced at runtime by a `BootstrapIncompleteError` self-check in `python -m runner poll`. (iii) New **§8 Repository Privacy & Security Posture** formalises the public-repo decision, mandates GitHub-Secrets-only storage for all Jira credentials, bans `pull_request_target`, and adds an allow-list constraint on `logging_ext.py` to exclude user-content (`summary`, `description`) from INFO-level Layer 1 logs. (iv) New **§9 Implementation Roadmap** makes test-first mandatory — the M1 milestone (parametric validation of D1–D23 from [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md)) MUST precede any `rules.py` side-effecting handler; 12 milestones (M0–M12) enumerated. (v) Renumbered prior §8 Changelog to §10 to accommodate the two new sections. All five changes align with the Solo-User Profile ([`JiraImplementation.md §9`](./JiraImplementation.md)) and require no edits to [`LivingRequirements.md`](./LivingRequirements.md) or [`JiraImplementation.md`](./JiraImplementation.md).
- **v0.1.0 (2026-04-18)** — Initial specification. Formalises Posture J-C as drafted in the v0.7.8 platform-feasibility evaluation. Locks three refinement decisions: (i) Python 3.11 as runtime (over Node.js) for match/case dispatch + `tenacity` ergonomics + ecosystem stability; (ii) Jira System Config issue as the watermark substrate (over GH Actions Cache) for durability across user breaks and repo migrations; (iii) 5-minute cron polling as the primary event-ingest mechanism (over Cloudflare Worker relay), with the relay preserved in §7 as an opt-in future enhancement. Directly derived from [`LivingRequirements.md`](./LivingRequirements.md) v0.7.8 and [`JiraImplementation.md §9`](./JiraImplementation.md) v0.7.8. No changes required to either source document.
