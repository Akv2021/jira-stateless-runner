# Jira Stateless Runner Implementation Roadmap — Posture J-C

> **Status:** Tracking artifact · v0.1.0 · **Last updated:** 2026-04-18 · **Source of truth:** [`LivingRequirements.md`](./LivingRequirements.md) v0.7.8, [`JiraImplementation.md`](./JiraImplementation.md) v0.7.9, [`ExternalRunner.md`](./ExternalRunner.md) v0.1.1, [`JiraProvisioningGuide.md`](./JiraProvisioningGuide.md) v0.1.1, [`ImplementationTestMatrix.md`](./ImplementationTestMatrix.md) v0.7.7

This roadmap orchestrates the end-to-end execution of the Jira Stateless Runner "Posture J-C" system into four sequential-but-parallelisable phases. It integrates the **M0–M12 runner milestones** from [`ExternalRunner.md §9`](./ExternalRunner.md) with the **Part A–E setup phases** from [`JiraProvisioningGuide.md`](./JiraProvisioningGuide.md) into a single tracking timeline.

**Authority rule:** this document adds no requirements. If any instruction below conflicts with the normative specs above, those documents win and this roadmap is stale.

**Stateless-Runner principle (reaffirmation):** Jira is the **sole authoritative state substrate** — custom fields on Units, the Status × Lifecycle board, the Sprint-as-Working-Set mapping, the System Config issue's polling watermark, and the `idem:<hex>` idempotency labels. The External Runner on GitHub Actions is a **pure processor** — it reads events from Jira, dispatches via `match/case` to T1–T13 per [`LivingRequirements.md §5.2`](./LivingRequirements.md), and writes side-effects back to Jira with idempotent labels and audit comments. A fresh `git clone` of the runner on a new machine resumes operation with zero data loss because **nothing operational lives outside Jira**.

---

## Critical path & parallelism map

```
Phase 1 (Jira):       [1.A API]───[1.B UI]───[1.C verify]─┐
                                                          │
Phase 2 (Runner):  [M0]─[M1]─[M2]─[M3]─[M4]───────────────┤
                    scf  TESTS DISP HTTP  IDEM             │
                                                          ↓
Phase 3:                                [M5]─[M6]─[M7]─[M8]──[M9]
                                         R1   R2   R4  WATERMARK  LOG
                                                          ↓
Phase 4:                                          [M10]─[M11]─[M12]
                                                   YAML  SMOKE  CRON
```

**Critical chain:** M1 → M2 is the single contract gate that must be GREEN before any side-effecting code is written. **M8 is the first runner milestone that requires Phase 1 complete.** M0–M7 can proceed in parallel with Phase 1.

**Total timebox (solo, evenings + weekend):** ~3–4 weeks (~37 h effort).

| Phase | Effort | Elapsed (solo) | Parallelisable with |
|---|---|---|---|
| Phase 1 | 4–6 h | Day 1–2 | Phase 2 M0–M7 |
| Phase 2 (M0–M4) | ~15 h | Week 1 | Phase 1 |
| Phase 3 (M5–M9) | ~15 h | Week 2 | — (M8 needs Phase 1 ✅) |
| Phase 4 (M10–M12) | 2–3 h | Day 15 | — |

---

## Phase 1 — Jira State Substrate Provisioning

**Goal:** Provision Jira Cloud Free as the authoritative state substrate. **Output:** two projects (`COREPREP`, `EXTENDED`), 16 custom fields, 7 v0.7.9 JQL filters, 2 System Config issues (one per project) labelled `runner-system`, 4 Automation rules per project (Rule 3 + §9.1 Archive/Pause/Resume buttons), a configured Scrum board with `Active / Paused / Archived` swimlanes, a started Sprint.

**Timebox:** 4–6 hours total. **Can start:** immediately. **Blocks:** Phase 3 M8 and Phase 4 M11.

### 1.A — API-automated provisioning ([Guide Part A](./JiraProvisioningGuide.md))

**Entry criteria:** Atlassian API token generated per [Guide §0.2–0.4](./JiraProvisioningGuide.md); site-admin role confirmed; `JIRA_ACCOUNT_ID` recorded.

| Step | Action | Source | Est. |
|---|---|---|---|
| 1.A.1 | Create projects `CORE-PREP`, `EXTENDED` (Scrum template) via `POST /rest/api/3/project` | [Guide §A.1](./JiraProvisioningGuide.md) | 10 min |
| 1.A.2 | Provision 16 custom fields (`Stage`, `Work Type`, `Lifecycle`, `Difficulty`, `Revision Target`, `Revision Done`, `Outcome`, `Has Had Test`, `Last Worked At`, `Last Transitioned At`, `Paused At`, + 5 System Config fields) via `POST /rest/api/3/field` | [Guide §A.2](./JiraProvisioningGuide.md) | 30 min |
| 1.A.3 | Attach fields to `Problem / Concept / Implementation / Pattern / Debug` issue-type screens; System Config fields → `Task` only | [Guide §A.3](./JiraProvisioningGuide.md) | 20 min |
| 1.A.4 | Create **System Config issue** per project with `labels: ["runner-system", "hidden"]`; Phase 3 M8 populates the watermark on first poll | [Guide §A.4](./JiraProvisioningGuide.md) | 5 min |
| 1.A.5 | Create the **seven v0.7.9 saved JQL filters** via `POST /rest/api/3/filter` | [Guide §A.5](./JiraProvisioningGuide.md) | 15 min |
| 1.A.6 | Verify the six non-`IP-Now` filters all contain `AND labels != "runner-system"` | [Guide §A.5](./JiraProvisioningGuide.md) | 5 min |
| 1.A.7 | Create `Cycle 1 — bootstrap` Sprint per project via board REST endpoints | [Guide §A.6](./JiraProvisioningGuide.md) | 5 min |

**Filter set (v0.7.9):** `IP-Now` · `IP-Working-Set` · `IP-Stale` · `IP-Paused-FIFO` · `IP-Archive` · `IP-Velocity-LT` · `IP-Stale-Eligible`. All six non-`IP-Now` filters include `AND labels != "runner-system"`; `IP-Stale-Eligible` additionally includes `AND "Has Had Test" = false` per the Solo-User Profile ([`JiraImplementation.md §9.2`](./JiraImplementation.md)).

### 1.B — Manual UI configuration ([Guide Part B](./JiraProvisioningGuide.md))

**Entry criteria:** 1.A complete. Jira Cloud Free does not expose the Automation REST API; every rule below is configured by hand per project.

| Step | Rule | Trigger → Action | Source | Est. |
|---|---|---|---|---|
| 1.B.1 | **Rule 3** — Lifecycle field-change handler (T5/T6/T7/T8 timestamp writes + audit comment) | [Guide §B.1](./JiraProvisioningGuide.md) | 15 min × 2 = 30 min |
| 1.B.2 | **`Solo-Archive`** manual-trigger button (U12 → `Lifecycle := Archived` → T7) | [Guide §B.2](./JiraProvisioningGuide.md) | 5 min × 2 = 10 min |
| 1.B.3 | **`Solo-Pause`** manual-trigger button (U9 → `Lifecycle := Paused` → T8) | [Guide §B.3](./JiraProvisioningGuide.md) | 5 min × 2 = 10 min |
| 1.B.4 | **`Solo-Resume`** manual-trigger button (U10/U11 → `Lifecycle := Active` → T6 or T5) | [Guide §B.4](./JiraProvisioningGuide.md) | 5 min × 2 = 10 min |
| 1.B.5 | Notification scheme tuning (Issue commented: ON; Issue updated: OFF) to suppress Rule 3 audit-comment spam | [Guide §B.5](./JiraProvisioningGuide.md) | 5 min |
| 1.B.6 | Board swimlanes — query-based `Active / Paused / Archived` | [Guide §B.6](./JiraProvisioningGuide.md) | 10 min × 2 = 20 min |

**Rule 3 guard (critical):** the `Condition: labels does not contain runner-system` clause prevents Rule 3 from firing on the System Config issue itself ([Guide §B.1](./JiraProvisioningGuide.md)).

### 1.C — Phase 1 verification

Run rows **E1–E10** of the [Part E verification checklist](./JiraProvisioningGuide.md). Rows E11–E15 defer to Phase 4 / M11.

### Phase 1 — Definition of Done

- ✅ `CORE-PREP` and `EXTENDED` projects exist (Scrum template)
- ✅ 16 custom fields provisioned and attached to correct screens
- ✅ 2 System Config issues exist, each with `labels: ["runner-system", "hidden"]`
- ✅ 7 v0.7.9 saved JQL filters exist; six carry `AND labels != "runner-system"`; `IP-Stale-Eligible` carries `AND "Has Had Test" = false`
- ✅ Rule 3 + three §9.1 manual-trigger buttons configured per project, with `runner-system` exclusion guard
- ✅ Board swimlanes `Active / Paused / Archived` render; `Cycle 1 — bootstrap` Sprint started
- ✅ Clicking **Pause** on a test Unit fires Rule 3 → writes `Paused At` → posts audit comment
- ✅ Part E rows E1–E10 all ✅

**Phase 1 gate:** **Phase 3 M8 may now proceed.**

---

## Phase 2 — GitHub Runner Core Development (M0–M4)

**Goal:** Build the pure-function core of the runner — dispatch logic, HTTP client, Pydantic models, idempotency, audit comments — with **test-first validation of the 23-row dispatch matrix**. No side-effecting rule handlers yet.

**Timebox:** 5–7 evenings (~15 h). **Entry:** immediate (parallel to Phase 1). **Blocks:** Phase 3.

### Test-First mandate (from [`ExternalRunner.md §9.3`](./ExternalRunner.md))

> **Skipping M1 → M2 is forbidden.** The 23-row parametric test suite in `tests/test_state_machine.py` covering [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md) rows **D1–D23** MUST be authored and RED-collected against a `NotImplementedError` stub before any production `match/case` code lands. This is the contract gate that locks the dispatch table.

### M0 — Scaffold (~2 h)

| Deliverable | Reference |
|---|---|
| Public GitHub repo `jira-stateless-runner` created per [`ExternalRunner.md §8.1`](./ExternalRunner.md) (public-repo mandate) | §8.1 |
| Directory tree per [`ExternalRunner.md §2.1`](./ExternalRunner.md) — `runner/`, `tests/`, `.github/workflows/` | §2.1 |
| `pyproject.toml` with `httpx`, `tenacity`, `pydantic`, `python-dateutil`; dev deps `pytest`, `ruff`, `mypy` | §2.2 |
| `.gitleaks.toml` + `pre-commit` hook (credential-handling mandate) | §8.2 |
| Branch protection on `main`; fork-PR approval required on Actions | §8.2 |

**Exit:** `pytest` collects 0 tests; `ruff check` clean.


### M1 — Parametric dispatch tests (**CRITICAL**, ~1 evening)

**Entry:** M0 complete. **Per [`ExternalRunner.md §9.3`](./ExternalRunner.md): test-first is mandatory.**

| Deliverable | Reference |
|---|---|
| `tests/test_state_machine.py` with **23 `pytest.parametrize` cases** covering rows D1–D23 of [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md) | §9.2 M1 |
| Each case names its matrix row (`D1`…`D23`) in the `pytest.param` id for grep-ability in CI | §9.2 M1 |
| `runner/state_machine.py` contains a `dispatch()` stub that raises `NotImplementedError` — no logic yet | §9.2 M1 |

**Exit:** 23 tests collected; all 23 RED against the `NotImplementedError` stub. **No other module touched.** PR opened and reviewed **row-by-row against the matrix** before merging.

### M2 — `state_machine.py` implementation (~2 evenings)

**Entry:** M1 RED-baseline landed (23 tests collected, all raise).

| Deliverable | Reference |
|---|---|
| Implement `dispatch(tuple, Outcome) → TransitionID` using Python 3.11 `match/case` per [`ExternalRunner.md §2.3`](./ExternalRunner.md) | §2.3 |
| Each matrix row maps to exactly one `case` clause; case order mirrors D1–D23 row order | §9.2 M2 |

**Exit:** **23 / 23 GREEN**; no regressions; `ruff check` clean; `mypy --strict` clean on `state_machine.py`. **Dispatch contract locked.**

### M3 — `models.py` + `jira_client.py` (~2 evenings)

**Entry:** M2 GREEN.

| Deliverable | Reference |
|---|---|
| Pydantic types for `Unit`, `Subtask`, `ChangelogEvent`, `TransitionID`, `Outcome`, `Stage`, `Lifecycle`, `WorkType` in `runner/models.py` | §2.2 |
| `httpx`-based `runner/jira_client.py` with `tenacity` retry decorator covering `http_429` (Retry-After respect), `http_5xx` (3 retries, exp. backoff), `http_401` (fail-fast, no retry) | §6.2 |
| `tests/test_jira_client.py` — mocked `httpx.MockTransport` tests for each retry branch | §6.2 |

**Exit:** All three retry branches exercised in tests; `mypy --strict` clean.

### M4 — `idempotency.py` + `audit.py` (~1 evening)

**Entry:** M3 GREEN.

| Deliverable | Reference |
|---|---|
| `idempotency.compute_key(unit_key, event_id, transition_id) → str` producing `sha256(…)[:12]` | §5.3 |
| `idempotency.has_been_applied(unit, key) → bool` via Jira label lookup (`idem:<hex>`) | §5.3 |
| `audit.post(unit_key, transition, note=None)` writing the canonical Layer-2 template comment | §5.2 |
| `tests/test_idempotency.py` asserting replay-safety — identical `(unit, event, transition)` → no-op | §5.3 |

**Exit:** Replay-safety property asserted; comment formatter matches the canonical [`ExternalRunner.md §5.2`](./ExternalRunner.md) template byte-for-byte.

### Phase 2 — Definition of Done

- ✅ M0–M4 all merged to `main`
- ✅ **23/23** dispatch tests GREEN; every matrix row D1–D23 has a named parametrised case
- ✅ Three HTTP-retry branches (429 / 5xx / 401) covered by `httpx.MockTransport` tests
- ✅ Idempotency replay-safety asserted (duplicate `(unit, event, transition)` → no-op)
- ✅ Audit comment formatter matches the §5.2 template byte-for-byte
- ✅ `ruff check` and `mypy --strict` clean across `runner/`
- ✅ ≥40 unit tests total (23 dispatch + HTTP + models + idempotency + audit)

**Phase 2 gate:** Phase 3 may now proceed.

---

## Phase 3 — Rule Handlers & System Integration (M5–M9)

**Goal:** Plug the pure core from Phase 2 into the three side-effecting rule handlers, with watermark I/O, health monitoring, and privacy-safe structured logging. At the end of Phase 3, the runner is feature-complete against specification — only deployment remains.

**Timebox:** 5–7 evenings (~15 h). **Entry:** Phase 2 gate ✅. **Blocks:** Phase 4.

### M5 — `rules.py` Rule 1 (T1 + Difficulty fallback) (~1 evening)

**Entry:** M4 GREEN.

| Deliverable | Reference |
|---|---|
| `rule1_unit_created(event)` per [`ExternalRunner.md §4.1`](./ExternalRunner.md) | §4.1 |
| **Difficulty fallback:** if `Difficulty ∉ {Easy, Medium, Hard}`, seed `Revision Target := config.RevisionTargetDefault (=2)`; audit comment appends `Note: Difficulty missing at creation; RevisionTarget defaulted to 2 (Easy).` | §4.1 |
| Guard: Stage present (hard pre-state; silent skip if missing) | §4.1 |
| Idempotency check via `idem:<hex>` label before Sub-task creation | §5.3 |
| Integration tests with `pytest-httpx` mock Jira: Difficulty-present, Difficulty-missing, replay, Stage-missing | §9.2 M5 |

**Exit:** Both Difficulty paths create a Learn Sub-task; replay is a no-op; Stage-missing is a silent skip.

### M6 — `rules.py` Rule 2 (T2 / T3 / T4 / T12 / T13) (~2 evenings)

**Entry:** M5 GREEN.

| Deliverable | Reference |
|---|---|
| `rule2_subtask_done(event)` dispatching via `state_machine.dispatch()` to T2/T3/T4/T12/T13 | §4.1 |
| **Regress-first ordering** per [`ExternalRunner.md §5.5`](./ExternalRunner.md) — when `Outcome=Regress`, reset tuple before spawning new Sub-task | §5.5 |
| T4 auto-Pause writes `Lifecycle := Paused`, `Paused At := now` | §4.1 |
| T12/T13 reset `RevisionDone := 0`; T13 additionally writes LSC fields | §4.1 |
| End-to-end integration tests covering every T2/T3/T4/T12/T13 branch (one per dispatch-matrix row it exercises) | §9.2 M6 |

**Exit:** All branches pass; audit-comment template uniform across transitions; every side-effect carries an `idem:<hex>` label.

### M7 — `rules.py` Rule 4 / T9 stale scan (~1 evening)

**Entry:** M6 GREEN.

| Deliverable | Reference |
|---|---|
| `rule4_stale_scan()` queries the `IP-Stale-Eligible` JQL (provisioned in Phase 1.A.5) | §4.2 |
| Per match: creates `[Stage][Test]` Sub-task (due +2 bd); writes **`Has Had Test := true`** (durable lifetime flag, not per-event `idem:*`) | §4.2, [`JiraImplementation.md §9.2`](./JiraImplementation.md) |
| Writes `Last Stale Scan At := now` to System Config | §3.2 |
| Test asserting **lifetime idempotency** — a Unit with `Has Had Test = true` is not re-selected on a second scan | §9.2 M7 |

**Exit:** Second scan on the same Unit is a no-op; `Has Had Test` serves as the durable lifetime guard.

### M8 — `watermark.py` + System Config bootstrap self-check (~1 evening) — **needs Phase 1 complete**

**Entry:** M7 GREEN **AND** Phase 1 gate ✅.

| Deliverable | Reference |
|---|---|
| `watermark.read() → int` from System Config `Last Processed Changelog Id` (treats `None` as `0`) | §3.2 |
| `watermark.write(id: int)` + `Last Successful Poll At := now` + `Runner Version := 0.1.1` | §3.2 |
| **`BootstrapIncompleteError` self-check** — on first poll, run each user-facing filter against `labels = "runner-system"`; any hit > 0 → fail fast with a listing of un-amended filters | §3.3 |

**Exit:** Self-check passes on the Phase 1 provisioned site; a deliberately broken filter reproduces `BootstrapIncompleteError`.


### M9 — `health.py` + `logging_ext.py` (~1–2 evenings)

**Entry:** M8 GREEN.

| Deliverable | Reference |
|---|---|
| `health.with_health_tracking()` decorator incrementing `consecutive_failures` on raise; crosses threshold → `open_alert()` | §6.3, §6.4 |
| `open_alert()` uses `gh issue create` with the §6.4 template; mirrors URL to System Config `Open Alert Issue Url` | §6.4 |
| `maybe_close_alert()` closes after 3-run recovery streak | §6.4 |
| `logging_ext.StructuredFormatter` with `_ALLOWED_INFO_FIELDS` frozen-set per [`ExternalRunner.md §8.3`](./ExternalRunner.md) | §8.3 |
| `tests/test_logging.py` asserting `summary` / `description` / `comment` are filtered out of INFO payloads | §8.3 |

**Exit:** Mocked `gh` integration test demonstrates open→recovery→close cycle; privacy filter test green.

### Phase 3 — Definition of Done

- ✅ M5–M9 all merged to `main`
- ✅ Rule 1 covers both Difficulty-present and Difficulty-missing paths (fallback → `RevisionTarget = 2`)
- ✅ Rule 2 covers every T2 / T3 / T4 / T12 / T13 branch with regress-first ordering
- ✅ Rule 4 asserts lifetime idempotency via `Has Had Test = true`
- ✅ `BootstrapIncompleteError` reproducible against a deliberately broken filter
- ✅ Dead-man's-switch open→recovery→close cycle asserted in mocked-`gh` test
- ✅ INFO-level log filter strips `summary` / `description` / `comment` content
- ✅ ≥90 tests total across dispatch + HTTP + idempotency + rules + watermark + health + logging

**Phase 3 gate:** Phase 4 may now proceed.

---

## Phase 4 — Deployment & Smoke Testing (M10–M12)

**Goal:** Wire the runner to GitHub Actions, configure Repository Secrets, execute the Part D smoke test end-to-end against the live Phase 1 Jira site, and flip on the 5-minute cron.

**Timebox:** 1–2 evenings (~2–3 h). **Entry:** Phase 3 gate ✅ **AND** Phase 1 gate ✅.

### M10 — Workflow YAMLs (~1 h)

| Deliverable | Reference |
|---|---|
| `.github/workflows/poll-dispatch.yml` — `schedule: */5 * * * *` + `workflow_dispatch`; `concurrency: { group: runner-poll, cancel-in-progress: false }` | [`ExternalRunner.md §4.1`](./ExternalRunner.md) |
| `.github/workflows/stale-scan.yml` — `schedule: 0 10 * * MON` + `workflow_dispatch` | §4.2 |
| `.github/workflows/healthcheck.yml` — `schedule: 0 */6 * * *` | §6.5 |
| Each workflow runs `gitleaks` before `pip install -e .` | §8.2 |
| Python 3.11 job; `pytest` runs in CI on PRs (not in dispatch workflows) | §9.4 |

**Exit:** `gh workflow list` shows three `runner-*` workflows; all three succeed on a dry-run `workflow_dispatch`.

### M11 — GitHub Secrets + Part D Smoke Test (~1 h)

**Entry:** M10 complete.

**Step 1 — Configure Secrets** ([Guide Part C](./JiraProvisioningGuide.md)):

```bash
gh secret set JIRA_BASE_URL --body "https://<yoursite>.atlassian.net"
gh secret set JIRA_EMAIL    --body "<you>@example.com"
gh secret set JIRA_TOKEN    # interactive
```

**Step 2 — Execute the Part D Smoke Test** ([Guide Part D](./JiraProvisioningGuide.md)):

| Phase | Action | Expected |
|---|---|---|
| D.1 | `gh workflow run poll-dispatch.yml` (empty Jira) | Watermark init; `Last Successful Poll At` populated; `Runner Version = 0.1.1`; exit 0 in ~30 s |
| D.2 | Create pilot Unit (`Problem`, Stage=Intermediate, Difficulty=Medium) | Changelog event recorded in Jira |
| D.3 | `gh workflow run poll-dispatch.yml` | **Rule 1 / T1 fires** — `[Intermediate][Learn] — <summary>` Sub-task created; `Revision Target = 3` seeded; `[Runner][T1]` audit comment; `idem:<hex>` label |
| D.4 | Mark the Learn Sub-task Done with Outcome=Pass | Changelog event recorded |
| D.5 | `gh workflow run poll-dispatch.yml` | **Rule 2 / T2 fires** — `[Intermediate][Revise#1]` Sub-task created, due +2 bd; `Work Type := Revise`; `[Runner][T2]` audit comment |
| D.6 | **Difficulty-fallback check:** create a second pilot Unit without `Difficulty` | Rule 1 / T1 fires with fallback: `Revision Target = 2`; audit note `Difficulty missing at creation; RevisionTarget defaulted to 2 (Easy).` |
| D.7 | Click **Pause** on a Unit | Rule 3 fires T8: `Lifecycle = Paused`; `Paused At` set; Jira-side audit comment present |

**Exit:** D.1–D.7 all pass. [Guide Part E](./JiraProvisioningGuide.md) **E11–E15** ✅.

### M12 — Cron enablement (~5 min)

**Entry:** M11 complete (D.1–D.7 all ✅).

| Step | Action |
|---|---|
| 1 | Verify `poll-dispatch.yml` `schedule` block is not commented out |
| 2 | Wait for next `*/5` cron tick (≤5 min) |
| 3 | `gh run list --workflow=poll-dispatch.yml --limit 5` — all 5 most recent runs are `success` |
| 4 | Wait for first Monday 10:00 for `stale-scan` cron (or `workflow_dispatch` to verify sooner) |
| 5 | Wait 6 hours for first `healthcheck` cron (or `workflow_dispatch`) |

### Phase 4 — Definition of Done

- ✅ Three workflow YAMLs landed, each running `gitleaks` before install
- ✅ Repository Secrets (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_TOKEN`) configured
- ✅ Smoke test D.1–D.7 all pass against the live Phase 1 Jira site
- ✅ Part E rows E11–E15 all ✅
- ✅ **3 consecutive green cron runs** of `poll-dispatch.yml` observed
- ✅ First real user-created Unit → Rule 1 / T1 fires autonomously

**Phase 4 gate:** system is live.

---

## Cross-cutting requirements (apply to every milestone)

| Requirement | Origin | Compliance |
|---|---|---|
| **Idempotency on every side-effect** | [`ExternalRunner.md §5.3`](./ExternalRunner.md) | Every create/edit carries `idem:<sha256(unit\|event\|transition)[:12]>` |
| **Audit comment on every transition** | §5.2 | `[Runner][Tn] <short> · run: <id> · event: <id> · key: idem_<hex>` posted to parent Unit |
| **INFO-log allow-list** | §8.3 | `summary` / `description` / `comment` NEVER at INFO; DEBUG only |
| **`gitleaks` clean** | §8.2 | Every commit + every PR |
| **Mandatory filter-exclusion** | §3.3 | All six non-`IP-Now` filters include `AND labels != "runner-system"` |
| **Public-repo posture** | §8.1 | Repo visibility = public; fork-PR approval required |
| **Rule 3 stays in Jira Automation** | [`JiraImplementation.md §9.1`](./JiraImplementation.md) | Runner does NOT implement T5/T6/T7/T8 timestamp writes |
| **Stateless-Runner principle** | [`LivingRequirements.md`](./LivingRequirements.md), [`ExternalRunner.md §3`](./ExternalRunner.md) | Jira holds all authoritative state; runner carries no persistent state beyond the watermark mirrored from Jira |

---

## Risk register & rollback paths

| Risk | Likelihood | Mitigation / Rollback |
|---|---|---|
| Atlassian Automation UI changes; §9.1 trigger steps differ | Medium | Re-record manual button steps in [`JiraProvisioningGuide.md §B.2–B.4`](./JiraProvisioningGuide.md) |
| Jira Cloud Free tightens Automation features (no manual triggers) | Low-Medium | Fallback: move Rule 3 to `workflow_dispatch` per-Unit; ~2 evenings work |
| `httpx` / `tenacity` major-version bump breaks `jira_client.py` | Low | Pin versions in `pyproject.toml`; renovate bot for controlled upgrades |
| GH Actions public-repo unlimited-minutes policy changes | Low | Drop cron to `*/15`; UX degrades but remains functional |
| User rotates `JIRA_TOKEN` without updating Secret | Medium | Dead-man's-switch catches on first `401`; alert issue auto-opens with rotation instructions per §6.4 |
| M1 dispatch tests contradict [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md) | Unknown (discoverable at M2) | Matrix is the tighter spec — fix matrix first, then test |
| Time budget overrun in Phase 3 | Medium | Narrow M1 scope to D1–D9 only (MVP for Core-set transitions); mark D10–D23 follow-up. **Never skip test-first.** |

---

## First three concrete actions (if starting now)

1. **Create the `jira-stateless-runner` GitHub repo** (public) and scaffold per M0 — ~2 h.
2. **Generate the Atlassian API token** and verify against `/rest/api/3/myself` per [`JiraProvisioningGuide.md §0.2–0.3`](./JiraProvisioningGuide.md) — ~5 min.
3. **Write the M1 test suite** — port [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md) rows D1–D23 verbatim into `tests/test_state_machine.py` as `pytest.parametrize` cases; leave `state_machine.dispatch()` as `NotImplementedError` — ~1 evening.

After those three, Phase 1.A can run in parallel with M2 the next evening.

---

## Changelog

- **v0.1.0 (2026-04-18)** — Initial end-to-end execution roadmap. Integrates M0–M12 from [`ExternalRunner.md §9`](./ExternalRunner.md) v0.1.1 with Parts A–E from [`JiraProvisioningGuide.md`](./JiraProvisioningGuide.md) v0.1.1 into four phases: Phase 1 (Jira State Substrate Provisioning — API-automated Part A + manual UI Part B + verification Part E rows E1–E10); Phase 2 (GitHub Runner Core Development M0–M4, enforcing the test-first mandate via the 23-row D1–D23 parametric suite against [`ImplementationTestMatrix.md §3`](./ImplementationTestMatrix.md)); Phase 3 (Rule Handlers & System Integration M5–M9 covering Rule 1 with Difficulty fallback, Rule 2 T2–T4 + T12–T13 with regress-first ordering, Rule 4 T9 with `Has Had Test` lifetime idempotency, watermark + bootstrap self-check, and privacy-safe logging); Phase 4 (Deployment & Smoke Testing M10–M12 wiring Repository Secrets, executing Part D D.1–D.7, and enabling the `*/5` cron after 3 consecutive green runs). Reaffirms the Stateless-Runner principle and provides a Definition of Done per phase, a critical-path/parallelism map, a risk register with rollback paths, and a cross-cutting-requirements table. Non-normative; adds no requirements.
