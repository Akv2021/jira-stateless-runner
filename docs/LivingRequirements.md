# Living Requirements — Interview Prep Decision-Support System

> **Status:** Baseline v0.7.8 · **Last updated:** 2026-04-17
> **Supersedes:** `SystemRequirements.md` (principles and FRs consolidated; ambiguities resolved or logged).
> **Authority:** `LivingRequirements.md` is the sole source of truth. `Boards.md` is not reconciled against this document; any board-side drift is a board-side issue.
> **How to edit:** change one numbered item per revision; record the change in §11.

---

## 1. Purpose & Scope

A decision-support system for interview preparation that:
- Structures learning across a four-level domain hierarchy.
- Drives each Unit through a Learn → Revise → Pause → Resume → Upgrade lifecycle.
- Separates **execution tracking** (artifacts) from **knowledge storage** (external, e.g., GitHub).

Out of scope: content delivery, calendar scheduling, automated skill assessment.

---

## 2. Glossary (Authoritative Definitions)

| Term | Definition |
|---|---|
| **Set** | Top-level container for a Unit. Kinds: `Core`, `Extended`, `Independent`. Automation behavior and revision engine are bound directly to the Set kind (see §4). `Independent` Sets have a name (e.g., `google-top-150`) and are fully isolated — no revision history, metadata, or lifecycle state crosses Set boundaries. |
| **Subject** | Named domain within a Set. `Core` contains `DSA`, `HLD`, `LLD` (strict lifecycle). `Extended` contains `Tech`, `HR`, `Misc` (flexible lifecycle). `Independent` Sets may ignore Subject entirely. |
| **Topic** | Optional named area inside a Subject (e.g., `DP`). Not required on any Unit. |
| **Chapter** | Optional grouping within a Topic (e.g., `DP-1D`). Not required. |
| **Unit** | Smallest learning item. `UnitKind ∈ {Problem, Concept, Implementation, Pattern, Debug}`. |
| **Stage** | Unit depth level: `Beginner`, `Intermediate`, `Advanced`. Closed set. **All three are standard stages that support `Learn` and `Revise` work types.** `Advanced` is not terminal — only `Archived` (Lifecycle) is terminal. The stage-successor function used by T5 is `next(Beginner) = Intermediate`, `next(Intermediate) = Advanced`, `next(Advanced) = Advanced` (self-loop; T5 at `Advanced` restarts a fresh Learn chain at the same stage — see §5.2, OQ-20). |
| **Work Type** | Two levels, with a strict separation between **phase** and **activity**: <br>• `Unit.WorkType ∈ {Learn, Revise}` is the Unit's current lifecycle **phase** and is part of the state tuple (§5.1). It is changed **only** by state-machine transitions T2, T5, T6, T11. Note that `Test` is **not** a valid `Unit.WorkType` value — the Unit is always in either a learning or revising phase.<br>• `Subtask.WorkType ∈ {Learn, Revise, Test}` is the **activity** type of one Subtask attempt, set at creation and immutable thereafter. For chain-generated Subtasks (T1/T2/T3/T5/T6/T11) `Subtask.WorkType = Unit.WorkType`. `Test` Subtasks (ad hoc or T9-generated) are **maintenance-only**: their completion is tracked in `LastSubTaskCompletedAt` but they do **not** advance `Unit.WorkType`, do **not** advance `RevisionDone`, and do **not** write `LastTransitionedAt`. |
| **Lifecycle** | Unit progression state: `Active`, `Paused`, `Archived`. `Archived` is the sole terminal state and is only reached by explicit user decision (T7). The user may revive an Archived Unit by setting `Lifecycle = Active` (fires T5 or T6, per state tuple). |
| **Revision Target** | Required # revisions at current Stage. Default from central config by Difficulty: `Easy=2`, `Medium=3`, `Hard=4`, `unspecified=2`. User may override per Unit (T10). |
| **Revision Done** | # revisions completed at the current Stage. Resets on Stage upgrade. |
| **Revision Gap** | Fixed series `[2, 5, 11, 25]` days held in central config. Gap *n* = days from completion of revision *n−1* (or Learn, if *n=1*) to the due date of revision *n*. Supports up to `Target = 4` without collision. Individual Subtask `DueDate` may be edited directly as a normal field edit. |
| **Working Set** | User-selected subset of Active Units currently in-flight (≈ 10–15 day cycle). One active Working Set at a time. |
| **Staleness** | Runtime-computed condition: `now − LastSubTaskCompletedAt > Threshold` (default 90 days; central config). Not stored; not a Lifecycle change. Drives the Stale View (§12) and the weekly revive scan (T9). |
| **Subtask** | Concrete execution item under a Unit for one `Learn`, `Revise`, or `Test` attempt. Fields: `WorkType ∈ {Learn, Revise, Test}`, `Status ∈ {Backlog, Todo, InProgress, Done}`, `DueDate?`, `EffortPoints?`, `CreatedAt`, `CompletedAt?`, `Outcome?` (see **Outcome** below). `Title`, `Description`, and `Comments` are assumed baseline features of the underlying tracker (e.g., Jira) and are not redefined here. **Mandatory Title format** (v0.7): the revision index `#n` is included **only** for `Revise` Subtasks; `Learn` and `Test` Subtasks omit it. <br>• `Learn`: `[Stage][Learn] — {ParentUnit.Title}` — e.g., `[Beginner][Learn] — Two Sum`.<br>• `Revise`: `[Stage][Revise#n] — {ParentUnit.Title}` where `n = RevisionDone + 1` at creation time (1-indexed) — e.g., `[Intermediate][Revise#2] — Two Sum`.<br>• `Test`: `[Stage][Test] — {ParentUnit.Title}` — e.g., `[Advanced][Test] — Two Sum`.<br>`Stage` is read from `Unit.Stage` at Subtask creation. Auto-created Subtasks (T1, T2, T3, T5, T6, T9, T11, T12, T13) default to `Status = Todo`; the user may demote a Subtask to `Backlog` to deprioritize it without deleting it. |
| **Outcome** | Subtask-local single-write flag on `Revise` and `Test` Subtasks only (not `Learn`). Domain: `{Pass, Regress}`, default `Pass`, set by the user at `→ Done` and immutable thereafter. Drives dispatch (§5.5) between the Pass path (T2/T3/T4/Test-no-op) and the Regress path (T12/T13). Sealed on dispatch — a late edit to a Done Subtask's Outcome does **not** re-fire a transition. Introduced v0.7.6 (§3 Principle 7). |
| **Effort Point (SP)** | Unit of effort. v1: `1 SP = 30 min`; `Learn = 2 SP`, `Revise = 1 SP`, `Test = 2 SP`. Future 15-min granularity doubles all values (Learn = 4, Revise = 2, Test = 4) to avoid decimals. |
| **LastSubTaskCompletedAt** (LSC) | Timestamp on the Unit. Written exclusively by the dispatch rule (§5.5 step 2) on every `Subtask → Done` event, regardless of `WorkType`. Drives Staleness (§5.7) and T9 eligibility. Seeded to `CreatedAt` by T1. |
| **LastTransitionedAt** (LT) | Timestamp on the Unit. Written exclusively by transitions whose Timestamp Updates column (§5.2) lists it — T1, T2, T3, T4, T5, T6, T10, T11. Never written by T7, T8, T9, or by bare Subtask edits. Drives progress-velocity analytics (FR11, §12.6). Seeded to `CreatedAt` by T1. See §5.6 for rationale. |

---

## 3. Core Principles (Ordered; Lower # Wins on Conflict)

1. **Decision Support, not Enforcement** — the system auto-continues along deterministic paths (the Learn → Revise chain, and Resume once the user toggles `Lifecycle = Active`); only Pause (T8) and Archive (T7) require explicit user action. The user retains override authority at all times via `Lifecycle` edits, T10, and T11.
2. **Separation of Concerns** — artifacts hold raw working memory; canonical knowledge lives externally.
3. **Low Cognitive Load** — the next actionable Subtask must exist without the user having to create it.
4. **Deterministic Continuations** — given the state tuple, the next auto-created Subtask is fully predictable.
5. **Soft Guidance** — due dates are signals, not deadlines.
6. **T-shaped Progression (Soft Heuristic Only)** — breadth precedes depth is a *suggestion* for Working Set composition and prioritization hints. It has **no** authority to block a user-initiated transition, an automated continuation (T2/T3/T4/T9), or any manual override (T10/T11). Violating P6 is permitted and unremarked by the system.
7. **Adaptive Regression** — a Revise or Test Subtask may complete with `Outcome = Regress` to signal recall/mastery failure; the revision chain then resets (`RevisionDone := 0`, Unit re-enters `Revise` phase) rather than advancing. This is the only authorized backward motion on the state tuple and is bounded to `RevisionDone` and `WorkType` — `Stage` and `Lifecycle` are untouched. Governs T12 / T13 (§5.2).

> Principle 1 reserves user authority for terminal (T7) and interruption (T8) transitions. All other transitions — including Resume — are deterministic given the state tuple, triggered by the user's `Lifecycle` toggle. Override of computed values remains available at any time via T10 / T11.

---

## 4. Domain Model

```
Set  (Core | Extended | Independent-<name>)
 └─ Subject           (required in Core; optional in Extended/Independent)
     └─ Topic?        (optional)
         └─ Chapter?  (optional)
             └─ Unit  (Problem | Concept | Implementation | Pattern | Debug)
```

**Set → automation binding:**

| Set Kind | Subjects | Automation / Revision Engine |
|----------|----------|------------------------------|
| `Core` | `DSA`, `HLD`, `LLD` | Full state machine (T1–T11). Strict lifecycle; non-negotiable. |
| `Extended` | `Tech`, `HR`, `Misc` | Full state machine (T1–T11). User commonly terminates the Revise chain early by lowering `RevisionTarget` via T10. |
| `Independent-<name>` | Free-form | Bypassed. Only T1, Subtask `Done`, T7 (Archive), and T8 (Pause) apply. No auto-chain (T2/T3), no auto-pause (T4), no resume rules (T5/T6), no stale scan (T9). |

Each Unit carries: `Set`, `Subject?`, `Topic?`, `Chapter?`, `UnitKind`, `Stage`, `WorkType` (phase), `Lifecycle`, `RevisionTarget`, `RevisionDone`, `Difficulty?`, `CreatedAt`, `LastSubTaskCompletedAt`, `LastTransitionedAt`, `PausedAt?`. `Title`, `Description`, and `Comments` come from the underlying tracker and are not redefined.

**Field-seeding rule (T1):** `RevisionTarget` is deterministically seeded at Unit creation from central config keyed by `Difficulty` (`Easy → 2`, `Medium → 3`, `Hard → 4`, `unspecified → 2` — see §2). It is never user-input at creation time; user overrides arrive later via T10. This keeps the creation path deterministic and free of modal forms.

**Execution-artifact separation:** The Unit is a persistent **artifact** (raw working memory + lifecycle state); the Subtask is an **execution** record for one attempt (§5.4 Tier table). Canonical knowledge is stored externally (Principle 2). No Unit field ever mirrors a Subtask field, and a Unit carries no direct effort or burndown data — those live on Subtasks and are aggregated only through transitions (§5.5).

**Subtask outcome extension (v0.7.6):** `Revise` and `Test` Subtasks carry an additional field `Outcome ∈ {Pass, Regress}`, defaulted to `Pass` at creation and set by the user at `→ Done`. `Outcome` is immutable once written. `Outcome = Pass` dispatches to T2/T3/T4 (Revise) or the maintenance no-op (Test) as before; `Outcome = Regress` activates T12 (Revise) or T13 (Test) — see §5.2, §5.5. `Learn` Subtasks have no `Outcome` field: a failed learning attempt is modelled by leaving the Subtask in `Todo`/`InProgress` rather than completing it, so no automated regress path is needed on the Learn phase.

---

## 5. Unit State Model

### 5.1 State tuple
`(Stage, WorkType, Lifecycle, RevisionDone)` — all transitions are defined over this tuple.

### 5.2 Transition table

Notation: `LSC` = `LastSubTaskCompletedAt`; `LT` = `LastTransitionedAt`. Tuple fields are `(Stage, WorkType, Lifecycle, RevisionDone)`. Auto-created Subtask payloads follow the Title format in §2 (`#n` only for `Revise`, with `n = RevisionDone + 1` read *after* any tuple update this transition performs).

| # | Pre-state | Event | Post-state (tuple + new Subtask) | Timestamp Updates | Mode |
|---|-----------|-------|----------------------------------|-------------------|------|
| T1 | ∅ | Unit created | `(Beginner, Learn, Active, 0)` · Subtask `{WorkType=Learn, Status=Todo, Title="[Beginner][Learn] — "+Unit.Title, DueDate=null}` | `CreatedAt := now`; `LSC := now`; `LT := now` | Auto |
| T2 | `(S, Learn, Active, 0)` | Learn Subtask `→ Done` | `(S, Revise, Active, 0)` · Subtask `{WorkType=Revise, Status=Todo, Title="[S][Revise#1] — "+Unit.Title, DueDate=now+Gap[1]}` | `LT := now` (LSC is written by §5.5 step 2 on the triggering Subtask → Done) | Auto-create |
| T3 | `(S, Revise, Active, n)` · `n+1 < Target` | Revise Subtask `→ Done` | `(S, Revise, Active, n+1)` · Subtask `{WorkType=Revise, Status=Todo, Title="[S][Revise#(n+2)] — "+Unit.Title, DueDate=now+Gap[n+2]}` | `LT := now` (LSC via §5.5 step 2) | Auto-create |
| T4 | `(S, Revise, Active, Target−1)` | Revise Subtask `→ Done` | `(S, Revise, Paused, Target)` · (no new Subtask) | `LT := now`; `PausedAt := now` (LSC via §5.5 step 2) | Auto |
| T5 | `(S, *, Paused∪Archived, n)` · `n ≥ Target` · user sets `Lifecycle = Active` | Resume-Upgrade | `(next(S), Learn, Active, 0)` · Subtask `{WorkType=Learn, Status=Todo, Title="[next(S)][Learn] — "+Unit.Title, DueDate=null}` | `LT := now` | User-triggered (deterministic) |
| T6 | `(S, *, Paused∪Archived, n)` · `n < Target` · user sets `Lifecycle = Active` | Resume-Continue | `(S, Revise, Active, n)` · Subtask `{WorkType=Revise, Status=Todo, Title="[S][Revise#(n+1)] — "+Unit.Title, DueDate=now+Gap[n+1]}` | `LT := now` | User-triggered (deterministic) |
| T7 | `(*, *, *, *)` · user sets `Lifecycle = Archived` | Archive | `(*, —, Archived, *)` · (no new Subtask) | (none) | User-triggered (terminal) |
| T8 | `(*, *, Active, *)` · user sets `Lifecycle = Paused` | Pause | `(*, *, Paused, *)` · (no new Subtask) | `PausedAt := now` | User-triggered |
| T9 | `(*, *, Active, *)` · `now − LSC > Threshold` · **no `Test` Subtask has ever been created on this Unit in its history** (at any Stage, in any `Status` including `Done` and `Backlog`, and regardless of whether it was subsequently deleted from the tracker) · no outstanding `Todo`/`InProgress` Subtask on Unit | Weekly revive scan | (no tuple change) · Subtask `{WorkType=Test, Status=Todo, Title="[Stage][Test] — "+Unit.Title, DueDate=now+Gap[1]}` where `Stage` is read unchanged from the Unit | (none) — neither `LSC` nor `LT`; the Subtask's later completion writes `LSC` only | Auto-create |
| T10 | `(S, *, *, n)` · user edits `RevisionTarget` | Override target | same tuple with new `RevisionTarget`. If new `Target ≤ RevisionDone`, T4 cascades. | `LT := now` (plus any cascade-induced writes from T4) | Manual override |
| T11 | `(S, *, *, *)` · user edits `Stage` directly | Override stage | `(newS, Learn, Active, 0)` · Subtask `{WorkType=Learn, Status=Todo, Title="[newS][Learn] — "+Unit.Title, DueDate=null}` | `LT := now` | Manual override |
| T12 | `(S, Revise, Active, n)` · Revise Subtask `→ Done` with `Subtask.Outcome = Regress` | Revise-Regress | `(S, Revise, Active, 0)` · Subtask `{WorkType=Revise, Status=Todo, Title="[S][Revise#1] — "+Unit.Title, DueDate=now+Gap[1]}` | `LT := now` (LSC via §5.5 step 2) | Auto-create |
| T13 | `(S, *, Active, n)` · Test Subtask `→ Done` with `Subtask.Outcome = Regress` | Test-Regress | `(S, Revise, Active, 0)` · Subtask `{WorkType=Revise, Status=Todo, Title="[S][Revise#1] — "+Unit.Title, DueDate=now+Gap[1]}` | `LT := now` (LSC via §5.5 step 2) | Auto-create |

Notes on T5/T6/T9 and the Subtask `→ Done` dispatch:
- T5 and T6 are **deterministic** once the user toggles `Lifecycle = Active`. The branch is selected from the state tuple: `n ≥ Target` → Upgrade (T5), else Continue (T6). No confirmation prompt.
- **T9 is idempotent over the Unit's entire history.** The guard in T9's pre-state is historical, not current-state: once a `Test` Subtask has ever been *created* on a Unit, T9 will not fire on it again — even if that Subtask is later deleted, moved to `Backlog`, or completed. Implementation must therefore track Test-Subtask-creation as a durable property of the Unit (either a `HasHadTest` flag set on first Test-Subtask creation, or a query over the tracker's Subtask audit log). The stale scan fires on a given Unit at most once over its lifetime. Users who want to re-test must create the Subtask manually.
- **Completion of a `Test` Subtask (T9-generated or ad hoc) fires no transition.** Because `Subtask.WorkType = Test`, the pre-state matches neither T2 (which requires `Unit.WorkType = Learn`) nor T3/T4 (which require `Unit.WorkType = Revise`). Only `LSC` is written. No tuple change, no `LT` write, no `RevisionDone` advance. This is the "maintenance activity" semantic.
- **`LSC` is written exclusively by §5.5 step 2, on every Subtask `→ Done`, unconditionally.** The Timestamp Updates column in this table therefore omits `LSC` for transitions T2/T3/T4 (they are all Subtask-Done–triggered) — listing it there would falsely imply the transition itself is the writer. T1 is the only row that writes `LSC` directly, because T1 is triggered by Unit creation, not by a Subtask event.
- **`LT` is written only when the Unit's state tuple or lifecycle actually advances:** T1, T2, T3, T4 on the Subtask-Done path; T5, T6, T10, T11 on manual user actions; T12, T13 on the regress path (§3 Principle 7). T7 (Archive), T8 (Pause), and T9 (Test Subtask spawn) never write `LT`. See §5.5 for the full Subtask-to-Unit sync rule.
- **T12 and T13 are opt-in** (v0.7.6; §3 Principle 7). Default `Subtask.Outcome = Pass` preserves the v0.7.5 dispatch exactly (T2/T3/T4 for Revise, no-op for Test). Only explicit user assertion of `Outcome = Regress` at `→ Done` activates the regress path. T12 is the sole motion that decreases `RevisionDone`; T13 is the sole motion that changes `Unit.WorkType` from a non-Revise phase to `Revise` without a user `Lifecycle` toggle. Neither touches `Stage` or `Lifecycle`, so Resume-Upgrade semantics (T5) remain governed solely by user choice.
- **T13 does not reset T9's lifetime-idempotency guard.** Once T9 has consumed its one-shot on a Unit, a subsequent Regress re-entry via T13 (plus eventual new staleness) will **not** re-arm T9. Users who need another auto-generated `Test` Subtask must create it manually. This preserves the audit-surface contract of §5.7.
- **Stage successor `next(S)` used by T5.** `next(Beginner) = Intermediate`, `next(Intermediate) = Advanced`, **`next(Advanced) = Advanced`** (v0.7.7, OQ-20 resolution). T5 at `Stage = Advanced` therefore resets the tuple to `(Advanced, Learn, Active, 0)` and spawns a fresh Learn Subtask at `Advanced` — i.e. "restart Advanced" rather than a no-op or an out-of-domain error. This aligns with §2's statement that `Advanced` is not terminal (only `Archived` is) and formalises the recommendation previously deferred in [`JiraImplementation.md §8`](./JiraImplementation.md) row J4.

### 5.3 Applicability by Set
- **Core** — full suite T1–T13.
- **Extended** — full suite T1–T13; common pattern is to terminate the Revise chain early via T10 (lower `RevisionTarget`).
- **Independent** — only T1, Subtask `Done`, T7 (Archive), and T8 (Pause) apply. T2/T3/T4/T5/T6/T9/T12/T13 do not fire. T10 and T11 are unused (no Revise chain, no Stage progression). `Subtask.Outcome` is ignored on Independent Sets — Subtask `→ Done` writes `LSC` only.

**Auto-without-confirmation** covers T1, T2, T3, T4, T9, T12, and T13. All of these are reversible via T10/T11, by editing the affected Subtask, or by toggling `Lifecycle`.

### 5.4 Field Distribution Schema

Fields are split into two tiers by their lifetime and authority. The split is what makes the dual-timestamp model (§5.6) and the runtime Stale View (§5.7) coherent.

| Tier | Owner | Lifetime | Authority | Fields |
|------|-------|----------|-----------|--------|
| **Stateful / Persistent** | Unit | For the life of the Unit | Sole source of truth for lifecycle and progress. Read by every view and transition. | `Set`, `Subject?`, `Topic?`, `Chapter?`, `UnitKind`, **`Stage`**, **`WorkType`** (phase), **`Lifecycle`**, **`RevisionTarget`**, **`RevisionDone`**, `Difficulty?`, `CreatedAt`, **`LastSubTaskCompletedAt`**, **`LastTransitionedAt`**, `PausedAt?` |
| **Execution / Ephemeral** | Subtask | For the life of one attempt | Drives the Now/Due view and feeds completion events up to the Unit (§5.5). Not aggregated into Unit state except through explicit transitions. | `WorkType` (activity), `Status`, `DueDate?`, `EffortPoints?`, `CreatedAt`, `CompletedAt?`, `Outcome?` (Revise/Test only; default `Pass`, set at `→ Done`) |

Rules:
1. **No duplication.** A field on the Subtask never shadows a field on the Unit. `Subtask.WorkType` is activity, `Unit.WorkType` is phase — same name, different domain (see §2).
2. **Two — and only two — writers for Stateful fields:** (a) a transition in §5.2, which writes the tuple + `LT` + `PausedAt` + `CreatedAt` as specified in its Timestamp Updates column; and (b) §5.5 step 2, which writes `LSC` unconditionally on every `Subtask → Done` event. No Subtask Status or field edit — other than `→ Done` — touches any Stateful field.
3. **Tracker-native fields** (`Title`, `Description`, `Comments`) are metadata on either entity and are not part of either tier. They may be edited freely; they have no semantic effect on the state machine.
4. **`Outcome` is a Subtask-local flag**, written once at `→ Done` and immutable thereafter. It influences which transition the dispatch rule (§5.5) selects, but is never read after dispatch completes. Late edits to `Outcome` on a Done Subtask do not re-fire a transition — the Subtask's contribution to Unit state is sealed by dispatch. `Outcome` is not defined on `Learn` Subtasks (§4).

### 5.5 State Synchronization (Subtask-to-Unit dispatch)

**Only one event type propagates Subtask state up to the Unit: a Subtask transitioning to `Status = Done`.** All other Subtask edits (Status `Todo ↔ InProgress`, `Todo → Backlog`, `DueDate` edits, `EffortPoints` edits, title edits) are Subtask-local and fire no Unit-level transition.

On `Subtask → Done`, the system runs exactly these steps in order:
1. Set `Subtask.CompletedAt := now`.
2. Write `Unit.LastSubTaskCompletedAt := now` **unconditionally**.
3. Match `(Unit.Stage, Unit.WorkType, Unit.Lifecycle, Unit.RevisionDone)` + `Subtask.WorkType` + `Subtask.Outcome` against the pre-state column of §5.2. All transition matches additionally require `Unit.Lifecycle = Active` (per §5.2); a Subtask `→ Done` on a `Paused` or `Archived` Unit always falls through to the dangling branch below. `Subtask.Outcome = Regress` is checked **before** the Pass path, so T12/T13 pre-empt T3/T4 on the same tuple. Evaluation order (first match wins):
   - If `Unit.Lifecycle = Active` ∧ `Unit.WorkType = Revise` ∧ `Subtask.WorkType = Revise` ∧ `Subtask.Outcome = Regress` → fire **T12**.
   - If `Unit.Lifecycle = Active` ∧ `Subtask.WorkType = Test` ∧ `Subtask.Outcome = Regress` → fire **T13**.
   - If `Unit.Lifecycle = Active` ∧ `Unit.WorkType = Learn` ∧ `Subtask.WorkType = Learn` → fire **T2**. (Learn Subtasks have no `Outcome` — §4.)
   - If `Unit.Lifecycle = Active` ∧ `Unit.WorkType = Revise` ∧ `Subtask.WorkType = Revise` ∧ `Subtask.Outcome = Pass` ∧ `RevisionDone + 1 < RevisionTarget` → fire **T3**.
   - If `Unit.Lifecycle = Active` ∧ `Unit.WorkType = Revise` ∧ `Subtask.WorkType = Revise` ∧ `Subtask.Outcome = Pass` ∧ `RevisionDone + 1 = RevisionTarget` → fire **T4**.
   - If `Subtask.WorkType = Test` ∧ `Subtask.Outcome = Pass` → **no transition** (maintenance completion).
   - Otherwise → **no transition**; treat as a dangling completion (e.g., a `Revise` Subtask completed after T11 reset the Unit to `Learn` phase, or any `Learn` / `Revise` / `Test` Subtask completed on a `Paused` or `Archived` Unit — `Outcome` is ignored in this branch because §5.3 disables the regress path outside `Active`). The `LSC` write from step 2 still stands.
4. If a transition fired, apply its post-state and Timestamp Updates per §5.2.

This dispatch rule is the **sole** authority for translating execution events into state-machine events. It is what makes `Subtask.WorkType = Test` the switch between "progress" and "maintenance" — everything else follows mechanically.

### 5.6 Activity vs. Progress Tracking (dual-timestamp rationale)

Two timestamps on the Unit encode two distinct questions:

| Field | Answers | Fires on | Consumed by |
|-------|---------|----------|-------------|
| **`LastSubTaskCompletedAt`** (LSC) | "Has the user *engaged* with this Unit recently?" | Any `Subtask → Done` — Learn, Revise, **or Test**. Step 2 of the dispatch rule above. | Runtime Staleness (§5.7); T9 eligibility; Stale View (§12.3). |
| **`LastTransitionedAt`** (LT) | "Has this Unit *moved* on the state tuple recently?" | State-tuple changes, in either direction: T1, T2, T3, T4, T5, T6, T10, T11 (advance); **T12, T13 (regress)**. | Progress-velocity analytics (FR11, §12.6). |

Why the split — three concrete cases where a single timestamp would lie:
- **Ad-hoc `Test` completion** (`Outcome = Pass`) bumps LSC (the user did engage) but writes no LT (the Unit did not move). A single-timestamp model would either (a) inflate velocity with every maintenance ping, or (b) leave the Unit looking stale the day after the user reviewed it. Both are wrong.
- **T9 revive Subtask completion** (`Outcome = Pass`) is the same case, but system-initiated: it clears staleness without pretending progress was made.
- **User pauses at Target (T4)** writes both LSC and LT — real completion *and* real phase end. Then T8 (user-initiated pause) on a mid-chain Unit writes neither, because nothing completed and nothing moved; only `PausedAt` advances.

Principle: **LSC is the activity timer, LT is the tuple-motion timer.** The dispatch rule in §5.5 guarantees they diverge iff the completion is maintenance-only (Test + Pass) or a pure-status event (T7/T8/T9).

**Regress and progress share LT.** T12 and T13 write LT because the state tuple genuinely moved (`RevisionDone` decreased, or `WorkType` changed Test→Revise). The timestamp alone is therefore ambiguous on direction — an LT bump may represent either forward or backward motion. Progress-velocity analytics that care about the difference (FR11) must consult the underlying tuple delta (most simply: was `RevisionDone` higher or lower after the write?) rather than the timestamp alone. This is by design: LSC/LT carry activity-vs-motion semantics; direction is recovered from the tuple history.

### 5.7 Staleness Authority

Staleness is a **pure runtime projection** over LSC. There is no `IsStale` field, no stored flag, no weekly materialization into a status column. The full definition is:

```
Unit is Stale  ≡  (now − Unit.LastSubTaskCompletedAt) > AgingThreshold
```

- `AgingThreshold` lives in central config (FR4; default 90 days).
- Recomputed on every query that reads the Stale View (§12.3).
- LSC — the sole input to this projection — is written by exactly one code path (§5.5 step 2) on exactly one event (`Subtask → Done`). No other writer exists; no cache-invalidation or reset handshake is needed.
- T9's weekly scan is the **only** writer that reacts to staleness, and it does so by creating a *Subtask*, not by mutating a Unit field. That Subtask's eventual completion refreshes LSC through the normal dispatch rule, which organically clears the projection.
- **T9 fires at most once per Unit lifetime.** Once consumed (any `Test` Subtask ever created on the Unit, regardless of later Status or deletion), the Unit may remain Stale indefinitely in §12.3 without triggering further auto-action — the Stale View surfaces it; user action is required to re-engage. Full guard specification lives in §5.2 T9.
- Staleness is not a `Lifecycle` value. An Archived Unit and a Paused Unit can both be stale; Staleness and Lifecycle are orthogonal axes.

This design is what keeps the spec lean: the dispatch rule (§5.5) is the only code path that needs to understand "recent activity" at all. Everything else is a `SELECT` that subtracts two numbers.

---

## 6. Functional Requirements

- **FR1 — Hierarchical Planning.** CRUD at every level (Set, Subject, Topic, Chapter, Unit). Both `Topic` and `Chapter` are optional per Unit. Subject is required in `Core` and `Extended`; optional in `Independent`. Select a Working Set across a 10–15 day cycle.
- **FR2 — State Management.** Maintain the tuple in §5.1; every transition must appear in §5.2. Applicability by Set is governed by §5.3.
- **FR3 — Auto-Create Chain; Deterministic Resume; Adaptive Regression.** The system auto-creates the next Subtask for **T1, T2, T3 (the Learn→Revise chain), T5/T6 (Resume on `Lifecycle = Active` toggle), T11 (Stage override), T9 (stale revive; `WorkType = Test`), and T12/T13 (regress re-entry, on `Subtask.Outcome = Regress` at `→ Done`)**. Auto-created Subtasks default to `Status = Todo` and follow the v0.7 Title format in §2 (Subtask) — `#n` is emitted only for `Revise` Subtasks; `Learn` and `Test` Subtasks omit the revision index. The regress path (T12/T13) is opt-in: the user explicitly asserts `Outcome = Regress` at Subtask `→ Done`; default `Outcome = Pass` preserves the monotonic Learn→Revise chain (see §3 Principle 7, §5.2). Pause (T8) and Archive (T7) are the only transitions that require explicit user action. Overrides remain available at any time via T10 (`RevisionTarget`) or T11 (`Stage`).
- **FR4 — Central Configuration with Per-Unit Target/Stage Override.** Central config holds: Difficulty → `RevisionTarget` mapping (`Easy=2, Medium=3, Hard=4, default=2`), the fixed `RevisionGap` series `[2, 5, 11, 25]` days, aging threshold (90 days), and Effort Model (`1 SP = 30 min`; `Learn = 2 SP`, `Revise = 1 SP`, `Test = 2 SP`). Per-Unit override is limited to `RevisionTarget` (T10) and `Stage` (T11). Individual `DueDate` on a Subtask may be edited as a normal field edit (not a tracked transition).
- **FR5 — Pause / Resume / Archive.** Pause is either automatic at Target (T4) or user-initiated (T8). Resume is triggered by the user setting `Lifecycle = Active` on a `Paused` or `Archived` Unit; the Continue-vs-Upgrade branch is determined deterministically from `RevisionDone` vs `RevisionTarget` (T5 / T6). Archive (T7) is always user-triggered by setting `Lifecycle = Archived` and is the sole terminal state. Upgrade may also be initiated directly via T11 (edit `Stage`) or indirectly via T10 (lower `RevisionTarget`, which may trigger T4; user then toggles `Active` to fire T5).
- **FR6 — Working-Set Separation.** "Planning" (catalog) and "Execution" (Working Set) are distinct views. One Working Set is active at a time. Implementations SHOULD bind "Working Set" to whatever cycle-scoped grouping the tracker natively provides (e.g., an active Sprint in Jira Scrum, a Cycle in Linear). The spec does not require a dedicated domain object; the host tracker's native grouping is the normative implementation of this FR.
- **FR7 — Prioritization.** Key: `Unit.Stage` (Beginner > Intermediate > Advanced), then `Subtask.WorkType` (Learn > Revise > Test), then `Subtask.DueDate` asc (nulls last).
- **FR8 — Staleness & Weekly Revive; Test-Failure Re-entry.** Staleness is a runtime projection over `LastSubTaskCompletedAt` with authority in §5.7 (no stored flag). The weekly revive scan is transition T9 in §5.2 — payload, pre-state guards (including lifetime idempotency), and non-applicability to `Paused` / `Archived` / `Independent` Units are defined there. **When the T9-spawned `Test` Subtask completes with `Outcome = Regress`, transition T13 fires** (§5.2): the Unit re-enters the Revise chain with `RevisionDone := 0` and a fresh `Revise#1` Subtask. T13 does **not** re-arm T9's lifetime-idempotency guard — the Unit remains outside future auto-stale-scans and any subsequent re-testing is user-initiated. This FR is the user-facing contract; §5.2 + §5.7 are the normative mechanics.
- **FR9 — Artifact.** Each Unit maintains an append-only timeline of raw notes, mistakes, observations, questions via the standard ticket Comments stream provided by the underlying tracker.
- **FR10 — External Knowledge Links.** Links to canonical notes (e.g., GitHub paths) are captured as plain text in the ticket Description or Comments. There is no dedicated URL field and no migration-state tracking.
- **FR11 — Minimal Analytics.** Exactly: (a) completed work per period (effort points, bucketed by `Subtask.CompletedAt`), (b) distribution by `Unit.Stage` and `Subtask.WorkType`, (c) Active vs Paused backlog size, (d) revision-due load. Progress-velocity reports use `LastTransitionedAt` deltas. Reported per Set.
- **FR12 — Set Isolation.** Units in `Independent` Sets never inherit `RevisionDone`, `Stage`, `LastSubTaskCompletedAt`, `LastTransitionedAt`, or any lifecycle state from a Unit with the same Subject/Topic in another Set. Duplicate Units across Sets are treated as separate entities with no shared history.
- **FR13 — Archived Visibility.** `Archived` Units are excluded from all active queues (`Now/Due`, `Paused`, `Planning`) and from the stale revive scan (T9). They remain visible in the Stale View (§12) at runtime so the user can decide whether to revive (toggle `Lifecycle = Active` → fires T5 or T6), recategorize, or delete.

---

## 7. Non-Functional Requirements

**NFR1** Low Cognitive Load · **NFR2** Low Operational Friction · **NFR3** Central Configurability (revision target, gaps, aging threshold, effort model) · **NFR4** Scalability over months · **NFR5** Interruption Resilience · **NFR6** Flexibility across Unit Kinds · **NFR7** Soft Guidance · **NFR8** Deterministic Continuations.

---

## 8. Open Questions / Deferred Decisions

| ID | Question | Assumed default | Status | Source |
|----|----------|-----------------|--------|--------|
| OQ-1 | Is `Chapter` mandatory under Topic? | Optional; queryable as grouping axis | **Resolved** | v0.3 |
| OQ-2 | Functional difference between `Paused` and `Archived`? | `Paused` = eligible for Resume / Upgrade. `Archived` = sole terminal state, user-triggered (T7); excluded from active queues; appears only in Stale View (FR13). | **Resolved** | v0.4 |
| OQ-3 | Can user trigger Upgrade before Target? | Yes — via T10 (lower `RevisionTarget` → triggers T4) or T11 (edit `Stage` directly). | **Resolved (reversal of v0.2)** | v0.4 |
| OQ-4 | Retry semantics — dedicated transition or manual? | **Removed.** Retry is handled by manually incrementing `RevisionTarget` via T10. No dedicated transition. | **Resolved (feature dropped)** | v0.4 |
| OQ-5 | Working Set persistence across windows? | One cycle at a time | **Resolved** | Boards §5 |
| OQ-6 | `PauseReason` enum content? | **Removed.** Pause no longer carries a reason field. | **Resolved (feature dropped)** | v0.4 |
| OQ-7 | Default aging threshold? | 90 days | **Resolved** | Boards §9 |
| OQ-8 | Per-Unit override of Revision Target / Gap? | Override is restricted to `RevisionTarget` (T10) and `Stage` (T11). Gaps are central-only; direct `DueDate` edits on a Subtask are a plain field edit. | **Resolved** | v0.4 |
| OQ-9 | Project split modeled as Set or LifecycleMode? | `Set` is the sole vehicle. `LifecycleMode` field **removed in v0.5**; automation is bound directly to Set in §4. | **Resolved (field removed)** | v0.5 |
| OQ-10 | Do `Independent` Units bypass FR2/FR3? | Yes — only T1, Subtask `Done`, T7 (Archive), and T8 (Pause) apply. | **Resolved** | v0.5 |
| OQ-11 | Subtask as first-class entity? | Yes — `Status ∈ {Backlog, Todo, InProgress, Done}` (v0.5 added `Backlog` for user-demoted items), `DueDate?`, `EffortPoints?`. | **Resolved** | v0.5 |
| OQ-12 | Effort Model in v1? | Yes — `1 SP = 30 min`. `Learn = 2 SP`, `Revise = 1 SP`, `Test = 2 SP`. Doubles to `1 SP = 15 min` later without decimals. | **Resolved** | v0.4 |
| OQ-13 | "Resumed" surfacing semantics? | **Field removed.** `JustResumedAt` dropped because views are not modeled in this document. Freshness, when needed, is derived from Subtask `Status = Todo` and recency. | **Resolved (feature dropped)** | v0.4 |
| OQ-14 | Does the Stale scan materialize as a ticket? | **Revised in v0.5; refined in v0.6.** No persistent Stale Signal and no consolidated ticket. Staleness is runtime-computed from `LastSubTaskCompletedAt`. The weekly scan (T9) auto-creates a single **`Test`** Subtask on each eligible stale `Active` Unit, and fires at most once per Unit lifetime (D25). Completion resets `LastSubTaskCompletedAt` only — no tuple change, no `LastTransitionedAt` write. | **Resolved (v0.6 refinement)** | v0.6 |
| OQ-15 | Resume branching: deterministic vs. user-confirmed? | **Deterministic in v0.5.** Toggling `Lifecycle = Active` on a `Paused`/`Archived` Unit fires T5 (Upgrade) if `RevisionDone ≥ Target`, else T6 (Continue). No confirmation prompt. User authority lives at the toggle itself and at T10/T11. | **Resolved (v0.5 simplification)** | v0.5 |
| OQ-16 | Additional Unit Kinds? | Adopt `Pattern` and `Debug`. Reject `Template`. | **Resolved** | v0.4 |
| OQ-17 | Additional Work Types? | Adopt `Test` for stale-review/timed practice. Reject `Teach`, `Refactor`, `Solve-Blind`. | **Resolved** | v0.4 |
| OQ-18 | Handling of `Misc` category? | **Set removed.** `Misc` is now a Subject within `Extended` (alongside `Tech`, `HR`), inheriting `Flexible` mode. | **Resolved (restructured)** | v0.4 |
| OQ-19 | When the same problem appears in `Core` and an `Independent` Set? | Two separate Units, no shared history (FR12). | **Resolved** | v0.3 |
| OQ-20 | `next(Advanced)` — what does T5 do at `Stage = Advanced`? | **`next(Advanced) = Advanced`** (self-loop). T5 at `Advanced` resets the tuple to `(Advanced, Learn, Active, 0)` and spawns a fresh Learn Subtask at `Advanced` — "restart Advanced" semantics. Aligns with §2 ("`Advanced` is not terminal"), §5.2 T5 row, and [`JiraImplementation.md §8`](./JiraImplementation.md) row J4 (previously recorded as a deferred recommendation). | **Resolved (v0.7.7)** | v0.7.4 audit (surfaced) · v0.7.7 (resolved) |

---

## 9. Historical Contradictions & Watch-items

> **Scope note (v0.5).** `Boards.md` is no longer a reconciliation target. Items A1–A8 are retained as history only. New drift from the board side is a board-side concern.

### 9A. Historical reconciliation (frozen at v0.4)

- **A1** — Learn/Revise chain branching. Deterministic chain (T2–T4) adopted in v0.4.
- **A2** — Project / Set split. Resolved via `Set` in §4 (`LifecycleMode` later removed in v0.5).
- **A3** — No board view for `Archived`. Resolved by FR13 (v0.4); refined by §12 (v0.5).
- **A4** — User-initiated pause (T8) and retry were LivingReqs-only. Retry removed (OQ-4).
- **A5** — Effort Model gap. Resolved in v0.4 by FR4.
- **A6** — `JustResumedAt` — field removed (OQ-13).
- **A7** — Stale as work item vs. flag. Resolved in v0.4 by dual mechanism; **superseded in v0.5** by runtime staleness + auto-Revise Subtask (FR8).
- **A8** — Auto-create softens Principle 1. Principle rewritten in §3 (v0.4, refined v0.5).

### 9B. Watch-items (require discipline, not structural change)

- **W1** — Auto-chain (T2/T3) will continue to produce `Todo` Subtasks as long as the user keeps completing them on an abandoned Unit. Mitigation: user pauses (T8), archives (T7), or demotes pending Subtasks to `Backlog`.
- **W2** — `Test` Work Type has no auto-chain. Created ad hoc or by T9 (stale revive, v0.6+). If repeated testing is needed on a Unit that already consumed its T9 slot, the user must create subsequent Test Subtasks manually.
- **W3** — `RevisionGap = [2, 5, 11, 25]` is not difficulty-parameterized; all Units with `Target = 4` share Gap[3] = 25 days. Accept for v1.
- **W4 (new in v0.5)** — T9 skips Units with any outstanding `Todo`/`InProgress` Subtask, which means a user who hoards pending Subtasks suppresses stale revival indefinitely. Mitigation: the Stale View (§12) is computed at runtime and still surfaces the Unit; user can act manually.
- **W5 (new in v0.5)** — Toggling `Lifecycle = Active` on an `Archived` Unit is a single-click revival that fires T5/T6 deterministically. Users must not conflate "un-archive to browse" with "un-archive to work"; field edits are semantic.
- **W6 (new in v0.6)** — T9 idempotency is Unit-lifetime, not per-Stage. A Unit that received its one T9 Test Subtask at `Beginner` will not receive another at `Intermediate` or `Advanced`, even if it later goes stale at the upgraded Stage. Accepted trade-off: keeps the scan cheap and prevents ticket pile-up; users who want per-Stage stale-tests create them manually.
- **W7 (new in v0.6)** — Because `LastSubTaskCompletedAt` is written by any Subtask completion (including `Test`), a user who repeatedly creates ad-hoc Test Subtasks keeps a Unit permanently "fresh" without ever advancing its Stage or `RevisionDone`. This is intentional — `LastTransitionedAt` and §12.6 exist precisely to expose that pattern in analytics.

---

## 10. Deviations from `SystemRequirements.md`

| # | Deviation | Status | Rationale |
|---|-----------|--------|-----------|
| D1 | Principles given numeric precedence (§3) | Active | Breaks the Decision-Support ↔ Low-Friction tie. Rewritten in v0.4 to cover chain-automation. |
| D2 | FR3: auto-create chain + deterministic resume | **Revised in v0.5** | v0.4 kept Resume user-confirmed. v0.5 makes Resume deterministic on `Lifecycle = Active` toggle (T5/T6); only Pause (T8) and Archive (T7) require explicit action. |
| D3 | Added `Archived` lifecycle value | Active (T7 user-triggered from any state; revivable in v0.5 via T5/T6) | Terminal-by-convention; the user can always revive by setting `Lifecycle = Active`. |
| D4 | ~~`PauseReason` enum~~ | **Removed in v0.4** | Pause intent captured in free-form comments if needed. |
| D5 | ~~`KnowledgeState`~~ · ~~`ExternalNoteURL`~~ | **Both removed by v0.4** | External links live in standard ticket description/comments. |
| D6 | ~~Retry transition~~ | **Removed in v0.4** | Retry = manually raise `RevisionTarget` via T10. |
| D7 | Renamed "sprint / current working set" → **Working Set** | Active | Avoids collision with external sprint tooling. |
| D8 | T-shaped principle as soft heuristic (P6) | Active (hardened in v0.5) | v0.5: P6 explicitly has no authority to block user actions *or* automated continuations (T2/T3/T4/T9). |
| D9 | ~~Stale Signal + consolidated weekly ticket~~ | **Superseded in v0.5; refined in v0.6** | v0.5 replaced the stored signal with runtime Staleness + T9 auto-Subtask. v0.6 changes the T9 payload from `Revise` to **`Test`** and caps T9 at one firing per Unit lifetime (see D25). |
| D10 | Central config with per-Unit `RevisionTarget` / `Stage` override | Active | Override limited to T10/T11; gaps central-only; `DueDate` edits are normal field edits. |
| D11 | Transition table (§5.2) is the authoritative spec | Active | Replaces the informal mixed-axis sequence in the original. |
| D12 | ~~`JustResumedAt`~~ | **Removed in v0.4** | Depended on views; views now live in §12. |
| D13 | Introduced **Set** as top-level container | Active | v0.4 collapsed to `Core` / `Extended` / `Independent`; `Misc` is a Subject inside `Extended`. |
| D14 | `Subtask` as first-class entity | Active (status enum expanded in v0.5 to `{Backlog, Todo, InProgress, Done}`) | `Backlog` added so user can deprioritize an auto-created Subtask without deletion. |
| D15 | T10 (`RevisionTarget` override), T11 (`Stage` override) | Active | Two manual-override transitions. Gap override dropped in v0.4. |
| D16 | FR12 Set Isolation | Active | Prevents cross-contamination between Core and Independent tracks. |
| D17 | FR13 Archived Visibility | Active (refined in v0.5) | Archived excluded from all active queues and from T9; visible at runtime in Stale View (§12). |
| D18 | Added `Pattern`, `Debug` unit kinds; `Test` work type | Active (Debug renamed from `Postmortem`, Test renamed from `Mock/Test` in v0.5) | Per-round feedback; `Template`, `Teach`, `Refactor`, `Solve-Blind` rejected. |
| D19 | ~~`LifecycleMode` field~~ | **Removed in v0.5** | Automation is bound directly to `Set` (§4). One less derived field to keep in sync. |
| D20 | Staleness is runtime-computed, not stored | Active (driver renamed in v0.6 from `LastWorkedAt` → `LastSubTaskCompletedAt`) | No persistent Stale Signal. Stale View (§12) and T9 both recompute on demand. |
| D21 | T9 redesigned: auto-`Revise` Subtask per stale Unit | **Superseded in v0.6 by D25** | v0.5 semantics retained in history only. v0.6 swaps payload to `Test` and enforces lifetime idempotency. |
| D22 | Resume via `Lifecycle = Active` toggle (T5/T6 deterministic) | **Added in v0.5** | Eliminates the user-confirmation prompt; branch is computed from the state tuple. |
| D23 | `RevisionGap` extended to `[2, 5, 11, 25]` | **Added in v0.5** | Supports `Target = 4` (Hard difficulty) without reusing Gap[3]. Closes watch-item W3 from v0.4. |
| D24 | §12 Views & Pseudo-rules introduced | Active (extended in v0.6 with §12.6 Progress Velocity) | Documents Now/Due, Paused, Stale as first-class artifacts; v0.6 adds the analytics-velocity projection driven by `LastTransitionedAt`. |
| D25 | T9 auto-creates a **`Test`** Subtask (not `Revise`) and is idempotent per Unit lifetime | **Added in v0.6** | Swapping the payload to `Test` means stale revival resets the activity timer **without** advancing `RevisionDone` or firing a state transition — stale-scan becomes pure maintenance activity. The lifetime cap (at most one T9-created Test Subtask ever) prevents the scan from re-churning a Unit that the user has already declined to engage with; further testing is user-initiated. |
| D26 | `LastWorkedAt` split into `LastSubTaskCompletedAt` + `LastTransitionedAt` | **Added in v0.6** | `LastSubTaskCompletedAt` tracks any Subtask completion (drives Staleness/T9 — maintenance semantics). `LastTransitionedAt` tracks only state-tuple advancement (T1/T2/T3/T4/T5/T6/T10/T11 — progress semantics). Separation prevents ad-hoc testing or stale-refreshes from inflating progress-velocity analytics (FR11, §12.6). |
| D27 | Two-level `WorkType`: `Unit.WorkType` (phase) vs. `Subtask.WorkType` (activity) | **Added in v0.6** | `Subtask.WorkType` is set at creation and immutable; it may be `Test` even when `Unit.WorkType ∈ {Learn, Revise}`. Only state-machine transitions change `Unit.WorkType`. Removes the ambiguity that let a `Test` Subtask be read as a phase change. |
| D28 | Mandatory Subtask Title format | **Added in v0.6; refined in v0.7 (D30)** | v0.6 used `[Stage][WorkType#RevisionDone] — {Unit.Title}` uniformly. v0.7 drops the `#n` index for `Learn` and `Test` (where it carried no signal) and keeps it only for `Revise`, where it is 1-indexed (`n = RevisionDone + 1` at creation). |
| D29 | `Subtask.Title` / `Description` / `Comments` treated as tracker-native, not redefined | **Added in v0.6** | Avoids duplicating baseline Jira-like features in the spec; only `WorkType`, `Status`, `DueDate`, `EffortPoints`, `CreatedAt`, `CompletedAt` are domain fields. |
| D30 | v0.7 Subtask Title format: `#n` only for `Revise`; `Learn` / `Test` omit the index | **Added in v0.7** | `Learn` always carries `RevisionDone = 0` (reset on Stage upgrade) — the index added no information and cluttered the tracker. `Test` is maintenance-only and decoupled from `RevisionDone` — the index was actively misleading. The 1-indexed Revise form (`Revise#1`, `Revise#2`, …) matches how users think about "the 2nd revision". |
| D31 | Transition table (§5.2) splits **Timestamp Updates** into a dedicated column | **Added in v0.7** | Prior rows mixed post-state, subtask payload, and timestamp writes in one cell and got visually unreadable. The dedicated column makes the activity/progress split (D26) auditable at a glance: rows with only `LSC` are maintenance, rows with `LT` are progress, rows with neither are archival/informational. |
| D32 | §5.4 Field Distribution Schema (Stateful/Persistent vs. Execution/Ephemeral) | **Added in v0.7** | Formalizes which fields live on the Unit vs. the Subtask and which authority writes them. Rules: no duplication across tiers; no Stateful field written by a Subtask directly — all mutations flow through §5.2 (dispatched by §5.5). |
| D33 | §5.5 State Synchronization (Subtask-to-Unit dispatch rule) | **Added in v0.7** | Names the only event that bridges the two tiers (`Subtask → Done`) and spells out the 4-step dispatch, including the no-op branch for `Test` Subtasks and dangling Learn completions. Replaces the implicit behavior that was scattered across §5.2 notes in v0.6. |
| D34 | §5.6 Activity vs. Progress Tracking; §5.7 Staleness Authority | **Added in v0.7** | §5.6 gives the rationale for the dual-timestamp model (D26) with three concrete lie-cases a single timestamp would produce. §5.7 pins down that Staleness is pure runtime — no `IsStale` field, no materialization — and that T9 is its only writer-reactor, which writes a Subtask, never a Unit flag. |

---

## 11. Change Log

- **v0.7.8 (2026-04-17)** — **R2 + R3: Platform-mapping pass** (Audit_v0.7.6.md §5.2 R2 and R3, revised per user guidance). Two refinement items landed, both via revisions that moved cost from the normative spec into the platform-specific reference — no new domain objects, no new fields, no new transitions on [`LivingRequirements.md`](./LivingRequirements.md). **§6 FR6 (Working Set):** one-line appendix clarifying that the Working Set is bound to the host tracker's native cycle-scoped grouping (Jira Sprint, Linear Cycle). The originally-scoped `WorkingSet { id, name, createdAt, closedAt?, units: [] }` sub-model and its W1/W2 transitions were **rejected** — Jira Sprint already provides the single-active-cycle invariant, the close-time residuals prompt, and the historical record. **`CanonicalURL` field was also rejected** — per user guidance, the canonical repository is known by convention and any per-Unit link the user chooses to paste lives in the ticket body; a structured URL field would add edit-cost at every Unit for a benefit the ticket `Description` already provides at zero ceremony. FR10 therefore stays unchanged. **R3 (Solo-User Jira Profile) is landed entirely inside [`JiraImplementation.md`](./JiraImplementation.md) §9** — see that file for details. The Status-vs-Lifecycle **collapse** considered in the original R3 draft was rejected: the two axes are orthogonal (Lifecycle answers "will I invest more effort in this Unit?"; Status answers "what am I doing on this specific ticket right now?"), and the Columns = Status × Swimlanes = Lifecycle board projection remains the canonical layout. **Rationale:** Principle 3 (Low Cognitive Load) and the guiding heuristic "the more lightweight and automated the system is, the more it'll be followed" — any new field or new domain object in the spec must clear a bar that platform-native mechanisms (Sprint, Description) fail to clear. No state-machine changes, no field additions, no transition additions. [`ImplementationTestMatrix.md`](./ImplementationTestMatrix.md) is unchanged (no new transitions or writers to enumerate).
- **v0.7.7 (2026-04-17)** — **R1: Documentation consistency pass** (Audit_v0.7.6.md §5.2 R1). No state-machine behaviour changed; all edits are synchronisation of stale references surfaced by the v0.7.6 audit. **§2 Glossary:** added a new `Outcome` entry (single-write, `{Pass, Regress}`, Revise/Test only, sealed on dispatch) and extended the `Subtask` entry's field list to include `Outcome?`; extended the `Stage` entry with the normative `next(·)` function `next(Beginner)=Intermediate`, `next(Intermediate)=Advanced`, **`next(Advanced)=Advanced`**. Auto-created-Subtask enumeration in the `Subtask` entry extended to include T12 / T13. **§5.2 transition notes:** added a new note defining `next(S)` explicitly and spelling out that T5 at `Advanced` is a self-loop that restarts a fresh Learn chain (not a no-op, not an error). **§6 FR3:** enumeration corrected to list **T1, T2, T3, T5/T6, T11, T9, T12, T13** as the auto-create set; the regress path is explicitly labelled opt-in to make Principle 7 visible at the FR tier. **§6 FR8:** extended the user-facing contract to document that a T9-spawned `Test` Subtask completed with `Outcome = Regress` fires T13 and that T13 does not re-arm T9 — this was previously only derivable from §5.2 notes. **§8 Open Questions:** added **OQ-20** formally resolving `next(Advanced) = Advanced`. OQ-14 remains about stale-scan materialisation (its original subject); the implicit `next(Advanced)` question surfaced in the v0.7.4 audit (and previously tagged "OQ-14" in informal review notes) is the subject of the new OQ-20. **Rationale:** the v0.7.6 changelog explicitly listed these as follow-ups; all are pure documentation edits with zero behavioural impact. The derived references ([`ImplementationTestMatrix.md`](./ImplementationTestMatrix.md), [`JiraImplementation.md`](./JiraImplementation.md)) are regenerated in this same pass (independent files; see their respective headers for versioning). **Scope:** R1 only; Working Set lifecycle / `CanonicalURL` (R2) and the solo-user Jira profile (R3) are deferred.
- **v0.7.6 (2026-04-17)** — **Adaptive Regression** — integrated two patterns recovered from the legacy `.augment/rules/` bot (`revision-logic.md` and `test-management.md`) that were genuinely absent from the v0.7.5 state machine: the `total_miss` revision outcome (Fibonacci position reset to 0) and the "failed test → back to revision" re-entry rule. Both collapse to one mechanism: a user-asserted `Subtask.Outcome ∈ {Pass, Regress}` field set at `→ Done`. **§3:** added Principle 7 Adaptive Regression — the only authorized backward motion on the state tuple, bounded to `RevisionDone` and `WorkType`. **§4:** extended Subtask to carry `Outcome` on Revise/Test Subtasks only (Learn has no Outcome; a failed Learn is modelled by *not* marking Done). **§5.2:** added two transitions — **T12** (Revise-Regress: `(S, Revise, Active, n)` → `(S, Revise, Active, 0)` + new Revise#1) and **T13** (Test-Regress: `(S, *, Active, n)` → `(S, Revise, Active, 0)` + new Revise#1). Both write `LT` (tuple motion). T13 does **not** reset T9's lifetime-idempotency guard. **§5.3:** Core/Extended gain T12/T13; Independent Sets ignore `Outcome` (writes LSC only). **§5.4:** `Outcome` added to the Subtask tier with a Rule 4 clarifying its single-write / sealed-on-dispatch semantics. **§5.5:** dispatch gains two Regress bullets, evaluated **before** the Pass path (first-match-wins), so default `Outcome = Pass` preserves v0.7.5 behaviour exactly. **§5.6:** LT renamed from "advanced" to "tuple-motion"; added an explicit note that direction (advance vs. regress) is recovered from the tuple delta, not the timestamp. **Rationale:** the legacy's revision trichotomy (`fast_recall` / `missing_partial_details` / `total_miss`) exposed a real hole in the v0.7.5 spec — T3/T4 would cheerfully mark a Unit "done revising" after three consecutive recall failures because the chain was monotonic. Same hole at T9: a failed Test Subtask completed silently, leaving the stale Unit with no follow-up because T9 is one-shot. Principle 7 closes both holes with minimal surface area. **Rejected candidates (analysis log):** Topic maturity ladder (too heavy — new entity state), Depth Mode Deep/Skim per Topic (conflicts with per-Unit model), bridge problems (content generation; out of scope per §1), daily chunk score cap (viewing concern, not §3-5), backlog warning (§12 concern), daily streak (gamification; out of scope), test bundles (requires cross-Unit test scope; too large for this pass), user-notes propagation (already covered by FR9). **Known stale references (follow-up v0.7.7):** FR3 still enumerates "T1, T2, T3 (Learn→Revise), T5/T6, T11, T9" — needs T12/T13 added; FR8 needs a cross-reference to T13 for the Test-failure re-entry path; [`JiraImplementation.md §2`](./JiraImplementation.md) needs `Outcome` added to the custom-field table and §4 Rule 2 needs a Regress branch. All §§1–12 sections outside §3/§4/§5 are otherwise unchanged.
- **v0.7.5 (2026-04-17)** — **Externalised §14 (Jira-Specific Implementation Details)** to [`JiraImplementation.md`](./JiraImplementation.md), mirroring the §13 → `ImplementationTestMatrix.md` split from v0.7.3. §14 in this document now carries only a pointer, a one-paragraph scope summary, and an authority rule (§§1–13 win on conflict). The extracted file preserves all eight original sub-sections (§14.1 Project & Issue-Type Scheme; §14.2 Custom-Field Mapping; §14.3 Workflow — Status vs. Lifecycle separation; §14.4 Automation-for-Jira Rules — the four rules implementing T1 / T2-T4 / T5-T6 / T9 with full pseudocode; §14.5 JQL Filters and Saved Views, including the `IP-Stale-Eligible` T9 lifetime-idempotency filter; §14.6 Board Configuration for `Core` / `Extended` / `Independent` projects; §14.7 Knowledge-Storage Separation; §14.8 Deviations from IM5). All internal cross-references were retargeted from bare `§n` to `[`LivingRequirements.md §n`](./LivingRequirements.md)` form so the reference file reads coherently in isolation. **Rationale:** §14 is platform-specific and non-normative, with the same "derived reference" nature as the test matrix; hosting it separately keeps the requirements document tool-agnostic and makes the Jira reference easier to evolve against a changing Jira feature set without touching the normative spec. No §§1–13 content changed.
- **v0.7.4 (2026-04-17)** — Integrated insights from the Jira design iterations (`IM.md` / `IM2.md` / `IM3.md` / `IM4.md` / `IM5.md`). **§4 (Domain Model):** added an explicit field-seeding rule (`RevisionTarget` derives deterministically from `Difficulty` at T1 — `Easy=2`, `Medium=3`, `Hard=4`, default `2`) and a short "execution-artifact separation" paragraph consolidating §5.4's Tier-1/Tier-2 split for implementers. No field changes, no transition changes. **§14 (Jira-Specific Implementation Details, new, non-normative):** appended a 180-line reference distilled from IM4/IM5 with IM4/IM5 taking precedence where earlier iterations differ. Covers project & issue-type scheme (§14.1), custom-field-to-spec mapping (§14.2, two tables), workflow status and transition scoping (§14.3, `Lifecycle` deliberately out of the Jira workflow), the four automation rules that implement T1/T2-T4/T5-T6/T9 with full pseudocode (§14.4), a complete saved-filter JQL catalogue including the T9 lifetime-idempotency guard (§14.5), Scrum and Kanban board configurations for `Core` / `Extended` / `Independent` projects (§14.6), the knowledge-storage separation convention (§14.7), and a deviation table (§14.8) recording five places the current spec moves beyond IM5 — most notably the `Revise → Test` payload change for T9, the lifetime-idempotency guard, and the fact that §2 rejects IM5's "Advanced is terminal" stance. §14 has no authority over §§1–13 (§§1–13 win on conflict).
- **v0.7.3 (2026-04-17)** — Added and then **externalised** the Implementation Test Matrix to keep the normative specification focused. First drafted as an in-document §13 covering three matrices — Dispatch Branch Matrix (19 rows across `S.WorkType × U.Lifecycle × U.WorkType` with the Target-boundary sub-case), T9 Idempotency Truth Table (firing condition `(K ≠ Independent) ∧ (L = Active) ∧ S ∧ ¬H ∧ ¬O`, with 10 enumerated rows), and Timestamp Write Reference (14 paths grouped by event class, naming the sole writer for LSC / LT / `PausedAt` / `CreatedAt` / `RevisionDone` / `WorkType` / `Stage` / `Lifecycle` and deriving a test-author checklist). The matrix has been moved to [`ImplementationTestMatrix.md`](./ImplementationTestMatrix.md) as a derived, non-normative reference with an added reader's guide (§1) and Mermaid visualisations (§2: dispatch flowchart and Unit lifecycle state diagram). §13 in this document now carries only a pointer and an authority rule (§5 wins on conflict). **Rationale:** the matrix is re-derivable from §5.2 + §5.5 + §5.7, needs regeneration whenever those sections change, and bloats the normative document; hosting it separately makes the requirements document easier to audit and the matrix easier to evolve independently. No §1–§12 content changed.
- **v0.7.2 (2026-04-17)** — Pre-implementation sanity check. **§5.5 dispatch rule:** evaluation-order semantics made explicit ("first match wins"); each Learn/Revise bullet now explicitly includes `Unit.Lifecycle = Active` to match §5.2's pre-state columns unambiguously; the dangling-completion example corrected (T11 *resets* phase to Learn, it does not *advance* it — the fallthrough now lists the real cases: post-T11 orphan Subtasks and Subtasks completed on `Paused`/`Archived` Units). **T9 idempotency guard** reworded from "ever existed" to "ever been created … regardless of whether it was subsequently deleted", with an implementation note (durable `HasHadTest` flag or audit-log query). **§5.7** gains a normative bullet restating T9's once-per-lifetime cap and pointing back to §5.2. No state-machine behavior changed; all edits are clarifications.
- **v0.7.1 (2026-04-17)** — Audit pass over v0.7. **Consistency fixes:** §5.2 Timestamp Updates column for T2/T3/T4 no longer lists `LSC := now` (that write is authoritative in §5.5 step 2; listing it in the transition row implied a double-writer). §5.4 Rule 2 rewritten to name exactly two writers of Stateful fields: transitions in §5.2 and §5.5 step 2 for `LSC`. §5.7 now explicitly states LSC has a single-writer authority. **Redundancy removal:** FR8 collapsed from a full re-exposition to a short user-facing contract that defers to §5.2 (T9 row) and §5.7. Glossary entries for `LSC` / `LT` tightened to point at their single writer. No state-machine behavior changed.
- **v0.7 (2026-04-17)** — Mechanics-documentation pass. **Subtask Title format:** `#n` index kept only for `Revise` Subtasks (1-indexed: `n = RevisionDone + 1` at creation); `Learn` and `Test` Subtasks now use `[Stage][Learn] — …` / `[Stage][Test] — …` (§2, D30). **Transition table (§5.2):** restructured with a dedicated **Timestamp Updates** column; all `LSC` / `LT` / `PausedAt` writes moved out of the Post-state column (D31). Title strings in every row updated to the new format. **New sections:** §5.4 Field Distribution Schema (Stateful/Persistent vs. Execution/Ephemeral; D32); §5.5 State Synchronization — the Subtask-to-Unit dispatch rule with explicit 4-step algorithm (D33); §5.6 Activity vs. Progress Tracking rationale; §5.7 Staleness Authority — staleness is a pure runtime projection, no stored flag (D34). **Glossary (§2):** clarified that `Unit.WorkType ∈ {Learn, Revise}` only (never `Test`) and that `Test` Subtasks are maintenance-only, with explicit consequences (no `Unit.WorkType` change, no `RevisionDone` advance, no `LT` write). **FR3** updated to reference the v0.7 Title format. No state-machine changes; no field additions or removals beyond Title-format formatting.
- **v0.6 (2026-04-17)** — Activity-vs-progress split. **Renamed field:** `LastWorkedAt` → `LastSubTaskCompletedAt` (fires on any Subtask `→ Done`; drives Staleness and T9). **Added field:** `LastTransitionedAt` (fires on T1/T2/T3/T4/T5/T6/T10/T11 only; drives progress-velocity analytics). **T9 redesigned:** payload changed from `Revise` Subtask to **`Test` Subtask**; added lifetime-idempotency guard — T9 fires at most once per Unit (any `Test` Subtask ever created, regardless of Stage or Status, blocks future firing); completion refreshes `LastSubTaskCompletedAt` only and performs no tuple change. **Subtask model:** introduced two-level `WorkType` (Unit-phase vs. Subtask-activity; §2, D27); mandatory Title format `[Stage][WorkType#RevisionDone] — {Unit.Title}` (§2, D28); `Title` / `Description` / `Comments` declared tracker-native (D29). **Transition table:** every row in §5.2 now lists explicit `LastSubTaskCompletedAt` / `LastTransitionedAt` / `PausedAt` writes, plus the exact auto-created Subtask payload. **FR updates:** FR3, FR7, FR8, FR11, FR12 aligned to the new timestamps and T9 payload. **Views:** §12.3 Stale driver renamed; §12.6 Progress Velocity added. **Deviations:** D9 refined; D20 timestamp driver renamed; D21 superseded; D24 extended; D25/D26/D27/D28/D29 added.
- **v0.5 (2026-04-17)** — Lean-model pass. **Removed fields:** `LifecycleMode` (automation bound to `Set` directly in §4); persistent Stale Signal. **Renamed:** `Postmortem` → `Debug` (UnitKind); `Mock/Test` → `Test` (WorkType). **Added field:** `Subtask.Status = Backlog` (user-deprioritize). **Revised transitions:** T5/T6 now deterministic, triggered by `Lifecycle = Active` toggle (no confirmation prompt); T7 uses explicit `Lifecycle = Archived`; T8 uses explicit `Lifecycle = Paused`; T9 redesigned to auto-create a `Revise` Subtask on each stale `Active` Unit (runtime staleness replaces the stored signal). **Config:** `RevisionGap` extended to `[2, 5, 11, 25]`. **Principles:** P1 rephrased for the deterministic-resume model; P6 (T-shaped) explicitly may not block user actions or automated continuations. **Structure:** §12 Views & Pseudo-rules added; §9 frozen against `Boards.md` (no further reconciliation). Resolved OQ-9/10/11/14/15 under the new model.
- **v0.4 (2026-04-17)** — Leaned the model. **Structure:** collapsed Sets to `Core / Extended / Independent`; `Misc` folded into `Extended` as a Subject. **Removed fields:** `ExternalNoteURL`, `PauseReason`, `JustResumedAt`, Subtask `Proposed` status. **Removed transitions:** retry (old T10), gap override (old T12). **Reworked:** FR3 (auto-create chain, manual branching at Resume/Archive); FR8 (weekly consolidated stale ticket); T7 (user-triggered archive from any state); added T11 for direct `Stage` override. **Added:** `Pattern` and `Debug` unit kinds; `Test` work type; FR13 Archived Visibility. **Principles:** P1 rewritten for chain-auto + branch-manual; P6 (T-shaped) explicitly non-overriding. Resolved OQ-2/3/4/6/8/11/12/13/14/15/16/17/18; no OQs remain open.
- **v0.3 (2026-04-17)** — Restructured around **Set** as top-level container (Core/Extended/Misc/Independent) and introduced FR12 Set Isolation. Removed `KnowledgeState` (D5 reverted). Added T11/T12 manual override transitions and `§5.3` mode applicability. Clarified `LastWorkedAt` (on `Done`) and `JustResumedAt` (on any `Proposed → Todo`). Formalized Effort Model (1 SP = 30 min). Resolved OQ-2/8/9/10/11/12/13/19; opened OQ-16/17/18.
- **v0.2 (2026-04-17)** — Cross-checked against `Boards.md`. Resolved OQ-1/3/5/7/8; partially resolved OQ-2/6; logged OQ-9 through OQ-15. Added `LifecycleMode`, `Subtask`, `JustResumedAt` to §2; scoped FR2 to `Managed` units; added §9 (new contradictions) and §10 (deviations).
- **v0.1 (2026-04-17)** — Initial baseline. Consolidated from `SystemRequirements.md`. Resolved the Decision-Support ↔ Auto-Generation conflict via principle precedence (§3) and the automation column of §5.2. Added explicit `Archived`, `KnowledgeState`, `PauseReason`, and retry (T10) transitions.

---

## 12. Views & Pseudo-rules

Views are UI/query projections; they hold no state and fire no transitions. Each is defined as a pseudo-rule over the current state. All views exclude `Independent` Units from automation-driven sections unless stated.

### 12.1 Now / Due
Purpose: what the user should work on right now.
```
Unit.Lifecycle = Active
  AND Unit has ≥1 Subtask S with
      S.Status = Todo
      AND (S.DueDate IS NULL OR S.DueDate ≤ now)
```
Ordering (per FR7): `Unit.Stage` asc (Beginner first), `Subtask.WorkType` (Learn > Revise > Test), `Subtask.DueDate` asc (nulls last).

### 12.2 Paused
Purpose: Units the user has stepped back from, candidates for Resume.
```
Unit.Lifecycle = Paused
```
Ordering: `PausedAt` desc (most recent first).

### 12.3 Stale
Purpose: runtime surfacing of Units whose `LastSubTaskCompletedAt` is older than `AgingThreshold` (central config, default 90 days). Recomputed on every query; no stored flag.
```
now − Unit.LastSubTaskCompletedAt > AgingThreshold
  AND Unit.Lifecycle ∈ {Active, Paused, Archived}
  AND Unit.Set.Kind ≠ Independent
```
Grouping: by `Lifecycle`. The `Active` group is the T9 scan target — but T9 additionally requires that **no `Test` Subtask has ever existed on the Unit** (see FR8), so a Unit appearing here may still be skipped by T9 and can only be re-tested by a user-created Subtask. `Paused` / `Archived` groups never receive auto-creation; they require user-initiated revival via `Lifecycle = Active` (fires T5/T6).
Ordering: `LastSubTaskCompletedAt` asc (oldest first).

### 12.4 Planning (catalog)
Purpose: backlog browsing across Sets; independent of execution state.
```
Unit.Lifecycle ∈ {Active, Paused}
```
No ordering constraint; user-driven filters on `Set`, `Subject`, `Topic`, `Chapter`, `UnitKind`, `Difficulty`, `Stage`.

### 12.5 Working Set
Purpose: execution scope for the current 10–15 day cycle (FR6).
```
Unit.Lifecycle = Active
  AND Unit.Id ∈ CurrentWorkingSet.Members
```
One Working Set is active at a time. Membership is a user selection; the system does not auto-populate.

### 12.6 Progress Velocity (analytics)
Purpose: distinguish lifecycle progress from maintenance activity for FR11 reporting.
```
Δlifecycle(U, window) = count of transitions where
    T ∈ {T1, T2, T3, T4, T5, T6, T10, T11}
    AND T.firedAt ∈ window
    AND T.Unit = U
```
Derived from `LastTransitionedAt` writes; T9 completions and T7/T8 actions are deliberately excluded so that ad-hoc testing and user-initiated pause/archive do not inflate velocity metrics.


---

## 13. Implementation Test Matrix

The Implementation Test Matrix — a **derived, non-normative** reference for implementers and test authors — has been extracted into a dedicated file to keep this document focused on the normative specification. It contains the same three matrices previously hosted here (Dispatch Branch Matrix, T9 Idempotency Truth Table, Timestamp Write Reference), plus a reader's guide mapping the input columns to the §5.5 branches and Mermaid diagrams for the dispatch algorithm and the Unit lifecycle state machine.

See [`ImplementationTestMatrix.md`](./ImplementationTestMatrix.md).

**Authority:** if the derived matrix conflicts with §5 of this document, §5 wins and the matrix is stale. The matrix is re-generated (manually) whenever §5.2 or §5.5 changes.


---

## 14. Jira-Specific Implementation Details

The Jira-specific configuration layer — a **derived, non-normative** reference mapping the platform-agnostic state machine of §§2–12 onto concrete Jira project, issue-type, custom-field, workflow, automation, JQL, and board settings — has been extracted into a dedicated file to keep this document tool-agnostic. It is distilled from the design iterations `IM.md`, `IM2.md`, `IM3.md`, `IM4.md`, and `IM5.md`, with IM4 and IM5 taking precedence where earlier iterations differ.

See [`JiraImplementation.md`](./JiraImplementation.md).

**Authority:** nothing in the Jira reference adds or removes a requirement. If a row there conflicts with §§1–13 of this document, §§1–13 win and the reference is stale. A non-Jira implementation may satisfy §§1–13 with an entirely different tool layout — the extracted file exists only to accelerate a Jira rollout, not to constrain design choices.
