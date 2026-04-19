# Jira-Specific Implementation Details

> **Status:** Non-normative reference · v0.7.9 · **Last updated:** 2026-04-18 · **Source of truth:** [`LivingRequirements.md`](./LivingRequirements.md) v0.7.8

This document maps the platform-agnostic state machine of [`LivingRequirements.md`](./LivingRequirements.md) §§2–12 onto a concrete Jira configuration. It is distilled from the design iterations in `IM.md`, `IM2.md`, `IM3.md`, `IM4.md`, and `IM5.md` — with IM4 and IM5 taking precedence where they differ from earlier drafts.

**Authority rule:** nothing in this document adds or removes a requirement. If a row here conflicts with [`LivingRequirements.md`](./LivingRequirements.md) §§1–13, the main document wins and this reference is stale. A non-Jira implementation may satisfy [`LivingRequirements.md`](./LivingRequirements.md) §§1–13 with an entirely different tool layout.

---

## 1. Project & Issue-Type Scheme

The core–extended–independent Set split ([`LivingRequirements.md §4`](./LivingRequirements.md)) is implemented as **Jira projects**, not issue-type subsets. This keeps board, automation, and analytics scopes cleanly separated and matches the Set Isolation contract ([`LivingRequirements.md §6 FR12`](./LivingRequirements.md)).

| Set Kind | Jira Project Key | Populated Subjects |
|---|---|---|
| `Core` | `CORE-PREP` | `DSA`, `HLD`, `LLD` |
| `Extended` | `EXTENDED` | `Tech`, `HR`, `Misc` |
| `Independent-<name>` | e.g., `GOOGLE150`, `AMAZON200` | free-form |

**Issue Type Scheme (IM3 §1.2; IM4 §4; IM5 §4):**

| Domain construct | Jira type | Notes |
|---|---|---|
| `Unit` (`UnitKind = Problem`) | `Problem` (Task-derived) | DSA-style solvable artifact |
| `Unit` (`UnitKind = Concept`) | `Concept` (Task-derived) | Theory / readings |
| `Unit` (`UnitKind = Implementation`) | `Implementation` (Task-derived) | LLD / HLD coding artifacts |
| `Unit` (`UnitKind = Pattern`) | `Pattern` (Task-derived) | Cross-cutting templates |
| `Unit` (`UnitKind = Debug`) | `Debug` (Task-derived) | Postmortem artifacts |
| `Subtask` | Jira `Sub-task` | One per `Learn` / `Revise` / `Test` attempt; never nested |

**Hierarchy rule (IM4 §4):** Parent Units are **never** placed in a sprint and **never** carry Story Points. Only Sub-tasks enter sprints and carry effort. This is what makes velocity analytics ([`LivingRequirements.md §6 FR11`](./LivingRequirements.md), [`§12.6`](./LivingRequirements.md)) correspond to real execution.

---

## 2. Custom Field Mappings

Custom fields are added to the `Problem`/`Concept`/`Implementation`/`Pattern`/`Debug` issue types. None of these are added to `Sub-task`. Tracker-native fields (`Summary`, `Description`, `Comments`, `Status`, `Story Points`, `Due Date`, `Priority`, `Sprint`) are reused as-is per [`LivingRequirements.md §5.4`](./LivingRequirements.md) rule 3.

| Custom Field | Jira type | Issue types | Maps to ([`LivingRequirements.md §2`](./LivingRequirements.md)) | Notes / Source |
|---|---|---|---|---|
| `Set` | Select (single) | Parent | `Set` | Optional; project key typically encodes this. IM5 §4. |
| `Subject` | Select (single) or Component | Parent | `Subject` | Use Components for cross-project reporting; Select for project-local. IM1–IM5. |
| `Topic` | Label | Parent | `Topic` | Free-form label (e.g., `DP`, `Graphs`). IM2 §2.2. |
| `Chapter` | Label or Select | Parent | `Chapter` | Free-form grouping within Topic (e.g., `DP-1D`). IM2 §2.2; IM4 §4. |
| `Stage` | Select (single) | Parent | `Stage` | Options: `Beginner`, `Intermediate`, `Advanced`. IM1–IM5. |
| `Work Type` | Select (single) | Parent | `Unit.WorkType` | Options: `Learn`, `Revise` only (never `Test` — IM4 §6.2; [`LivingRequirements.md §2`](./LivingRequirements.md)). |
| `Lifecycle` | Select (single) | Parent | `Lifecycle` | Options: `Active`, `Paused`, `Archived`. Default `Active` on create. IM1–IM5. |
| `Revision Target` | Number | Parent | `RevisionTarget` | Seeded from `Difficulty` at T1 ([`LivingRequirements.md §4`](./LivingRequirements.md) field-seeding rule). IM4 §7.1. |
| `Revision Done` | Number | Parent | `RevisionDone` | Default 0; incremented only by Rule 2 on `Revise` Subtask completion. IM4 §7. |
| `Difficulty` | Select (single) | Parent | `Difficulty?` | Options: `Easy`, `Medium`, `Hard`. Drives `Revision Target` seeding only. IM4 §7.1. |
| `Paused At` | DateTime | Parent | `PausedAt?` | Written by T4 / T8; cleared (null) on resume. IM1–IM5. |
| `Last Worked At` | DateTime | Parent | `LastSubTaskCompletedAt` (LSC) | Renamed in [`LivingRequirements.md`](./LivingRequirements.md) v0.6; Jira field name retained. IM2 §2.6. |
| `Last Transitioned At` | DateTime | Parent | `LastTransitionedAt` (LT) | New in [`LivingRequirements.md`](./LivingRequirements.md) v0.6 — no direct IM precursor. See [`LivingRequirements.md §2`](./LivingRequirements.md), [`§5.6`](./LivingRequirements.md). |
| `Unit Kind` | Select (single) | Parent | `UnitKind` | Redundant with issue type but useful for cross-type filters. Optional. |

**Subtask-side mapping (IM3 §2; IM4 §10):**

| Subtask field ([`LivingRequirements.md §2`](./LivingRequirements.md)) | Jira field | Notes |
|---|---|---|
| `WorkType` | **Label** on the Sub-task | Values: `learn`, `revise`, `test` (lowercase). Encoded in label because Sub-tasks do not carry custom fields in IM4/IM5. |
| `Status` | Jira `Status` | Mapped 1:1 to workflow (§3 below). |
| `DueDate?` | Jira `Due Date` | Set at creation per `RevisionGap` table. |
| `EffortPoints?` | `Story Points` | `Learn = 2`, `Revise = 1`, `Test = 2` per [`LivingRequirements.md §2`](./LivingRequirements.md) Effort Point. |
| `CreatedAt` / `CompletedAt?` | Jira `Created` / `Resolved` | Tracker-native. |
| `Outcome?` | `Outcome` — **Select (single)** custom field on `Sub-task` | Options: `Pass`, `Regress`. Default `Pass`. Visible only on `Revise` and `Test` Sub-tasks (enforced by field-context scoping to Sub-tasks with `labels ∈ {revise, test}`). Set by the user at the time of `→ Done` and **sealed thereafter** — post-Done edits have no automation effect (Rule 2 reads `Outcome` only on the Done-transition trigger). Absent on `Learn` Sub-tasks (matches [`LivingRequirements.md §4`](./LivingRequirements.md) v0.7.6 extension). Added in v0.7.7. |

The Subtask's `Stage` and `Revise#n` parts of its mandatory Title are rendered at creation time from the parent's custom fields — they are not stored separately on the Sub-task.

**Note on Sub-task custom fields.** `Outcome` is the only custom field on `Sub-task` in this implementation; all other Sub-task data rides on tracker-native fields or the label set. If your Jira instance disallows custom fields on Sub-tasks, fall back to a second label pair (`outcome-pass` / `outcome-regress`) applied at `→ Done`; Rule 2 branches then read the label instead of the field.

---

## 3. Workflow Status Names & Transitions

A single workflow covers both Parent types and Sub-tasks (IM3 §3; IM4 §11):

| Jira Status | Maps to ([`LivingRequirements.md §2`](./LivingRequirements.md)) | Applies to |
|---|---|---|
| `Backlog` | `Subtask.Status = Backlog` | Sub-task (deprioritized); Parent (not yet started) |
| `To Do` | `Subtask.Status = Todo` | Sub-task |
| `In Progress` | `Subtask.Status = InProgress` | Sub-task |
| `Done` | `Subtask.Status = Done` | Sub-task |

**Lifecycle is a custom field, not a workflow status.** This is deliberate (IM3 §3.1; IM4 §6.3): Jira's `Status` column is used for sprint execution (the Sub-task workflow), while Parent-level lifecycle `Active`/`Paused`/`Archived` is a separate orthogonal axis that drives automation (Rules 3 and 4). Mixing the two into a single workflow was rejected in IM3 because it collides with sprint analytics.

**Transition screen rules:**
- `To Do → In Progress` and `In Progress → Done` are always available on Sub-tasks.
- `Todo → Backlog` requires no confirmation (user deprioritization).
- `Backlog → Todo` is allowed (re-prioritization) and fires no automation.
- Parent Status transitions are not used by any automation — only Parent `Lifecycle` changes trigger Rules 3 and 4.

---

## 4. Automation-for-Jira Rules

The ten state-machine transitions T1–T11 collapse onto **four** automation rules in IM4 §13 / IM5 §13. This is possible because many transitions share the same Jira-side trigger (e.g., T2/T3/T4 all fire on the same `Sub-task → Done` event, and the rule branches internally on the Subtask label and the parent's `Revision Done`/`Revision Target`).

| Jira rule | Trigger | State-machine transition(s) covered | Branch logic |
|---|---|---|---|
| **Rule 1: Parent Created** | Issue `Created` on any Parent issue type | **T1** | Seed state tuple + spawn first `Learn` Sub-task |
| **Rule 2: Subtask Completion** | Sub-task `Status → Done` | **T2**, **T3**, **T4**, **T12**, **T13**, + maintenance-only `Test`+`Pass` path | Branch **first** on `Outcome = Regress` (→ T12 / T13), then on Sub-task label (`learn` / `revise` / `test`) and, for `revise`+`Pass`, on `Revision Done + 1 vs. Revision Target` |
| **Rule 3: Resume Engine** | Parent custom-field `Lifecycle` changed to `Active` (from `Paused` or `Archived`) | **T5**, **T6** | Branch on `Revision Done ≥ Revision Target` (→ T5 upgrade) vs. `<` (→ T6 continue) |
| **Rule 4: Weekly Stale Scan** | Scheduled (weekly) | **T9** | JQL-driven; creates one `Test` Sub-task per eligible Parent, guarded by lifetime idempotency |

**Transitions T7, T8, T10, T11 are not automated** — they are direct user edits of Parent fields (`Lifecycle`, `Revision Target`, `Stage`). No Jira rule fires on these; the field edit itself is the transition, and the `Last Transitioned At` write is accomplished via a small field-change automation that updates `Last Transitioned At := {{now}}` whenever any of `Stage`, `Work Type`, `Lifecycle`, or `Revision Target` changes on a Parent. This one-liner replaces eight separate rules.

### 4.1 Rule 1 — Parent Created (implements T1)

```
Trigger: Issue Created
  Condition: Issue Type in {Problem, Concept, Implementation, Pattern, Debug}
Actions:
  Edit fields on current issue:
    Stage           := "Beginner"
    Work Type       := "Learn"
    Lifecycle       := "Active"
    Revision Done   := 0
    Revision Target := lookup({{issue.Difficulty}}, Easy=2, Medium=3, Hard=4, default=2)
    Last Worked At        := {{now}}
    Last Transitioned At  := {{now}}
  Create sub-task:
    Summary       := "[Beginner][Learn] — {{issue.summary}}"
    Labels        := ["learn"]
    Story Points  := 2
    Due Date      := (unset)
```

(IM4 §13 Rule 1; IM5 §13 Rule 1. `Revision Target` seeding rule per [`LivingRequirements.md §4`](./LivingRequirements.md).)

### 4.2 Rule 2 — Subtask Completion (implements T2 / T3 / T4 / T12 / T13 and the `Test`+`Pass` no-op)

The evaluation order below mirrors [`LivingRequirements.md §5.5`](./LivingRequirements.md) step 3 exactly: Regress bullets first, then Pass path. `Outcome` absence on Learn Sub-tasks is treated as `Pass`-equivalent (Learn has no Regress path).

```
Trigger: Issue transitioned
  From: any → To: "Done"
  Condition: Issue Type = Sub-task
Actions:
  Edit fields on {{issue.parent}}:
    Last Worked At := {{now}}           // §5.5 step 2 — unconditional LSC write

  Guard: if parent.Lifecycle ≠ "Active"  →  stop  // §5.5 branch 5 (dangling)

  Set local var outcome := {{issue.Outcome}} default "Pass"

  Branch (first match wins — §5.5 evaluation order):

    // ---- Regress path (T12 / T13) — evaluated BEFORE Pass ----

    If "revise" in labels AND outcome = "Regress" AND parent.Work Type = "Revise":
      // T12: Revise-Regress — reset chain, stay in Revise phase
      Edit fields on parent:
        Revision Done        := 0
        Last Transitioned At := {{now}}
      Create sub-task:
        Summary      := "[{{parent.Stage}}][Revise#1] — {{parent.summary}}"
        Labels       := ["revise"]
        Story Points := 1
        Due Date     := {{now.plusBusinessDays(2)}}   // RevisionGap[1]
      Stop.

    If "test" in labels AND outcome = "Regress":
      // T13: Test-Regress — force Revise phase, reset chain
      // Applies at any parent.Work Type ∈ {Learn, Revise} (LivingRequirements.md §5.2 T13).
      // Does NOT reset the T9 lifetime-idempotency guard (§5.7).
      Edit fields on parent:
        Work Type            := "Revise"
        Revision Done        := 0
        Last Transitioned At := {{now}}
      Create sub-task:
        Summary      := "[{{parent.Stage}}][Revise#1] — {{parent.summary}}"
        Labels       := ["revise"]
        Story Points := 1
        Due Date     := {{now.plusBusinessDays(2)}}   // RevisionGap[1]
      Stop.

    // ---- Pass path (T2 / T3 / T4) — unchanged from v0.7.5 ----

    If "learn" in labels AND parent.Work Type = "Learn":
      // T2: Learn → Revise phase transition (Learn has no Outcome)
      Edit fields on parent:
        Work Type            := "Revise"
        Last Transitioned At := {{now}}
      Create sub-task:
        Summary      := "[{{parent.Stage}}][Revise#1] — {{parent.summary}}"
        Labels       := ["revise"]
        Story Points := 1
        Due Date     := {{now.plusBusinessDays(2)}}   // RevisionGap[1]

    If "revise" in labels AND outcome = "Pass" AND parent.Work Type = "Revise":
      Set local var done := {{parent.Revision Done}} + 1
      Edit fields on parent:
        Revision Done        := {{done}}
        Last Transitioned At := {{now}}
      If {{done}} < {{parent.Revision Target}}:
        // T3: next revise in chain
        Set local var gap := lookup({{done}}+1, 1=2, 2=5, 3=11, 4=25)   // business days; see LivingRequirements.md §2 RevisionGap
        Create sub-task:
          Summary      := "[{{parent.Stage}}][Revise#{{done + 1}}] — {{parent.summary}}"
          Labels       := ["revise"]
          Story Points := 1
          Due Date     := {{now.plusBusinessDays(gap)}}
      Else:
        // T4: target reached → auto-pause
        Edit fields on parent:
          Lifecycle := "Paused"
          Paused At := {{now}}

    If "test" in labels AND outcome = "Pass":
      // §5.5 branch 4 — maintenance completion, no transition
      (no further action; LSC already written above)

    Otherwise:
      // §5.5 branch 5 — dangling (cross-phase, post-T11 orphan)
      (no further action; LSC already written above)
```

(IM4 §13 Rule 2; IM5 §13 Rule 2; revision-gap table from [`LivingRequirements.md §2`](./LivingRequirements.md). Regress branches added in v0.7.7 per [`LivingRequirements.md §5.2`](./LivingRequirements.md) T12 / T13 and [`§5.5`](./LivingRequirements.md) first-match-wins evaluation order.)

### 4.3 Rule 3 — Resume Engine (implements T5 / T6)

```
Trigger: Field value changed — Lifecycle
  From: any, To: "Active"
  Condition: Issue Type in {Problem, Concept, Implementation, Pattern, Debug}
Actions:
  Edit fields on current issue:
    Last Transitioned At := {{now}}
  If {{Revision Done}} >= {{Revision Target}}:
    // T5: upgrade stage, new Learn chain
    Edit fields on current issue:
      Stage         := next({{Stage}})   // Beginner→Intermediate→Advanced→Advanced (self-loop per LivingRequirements.md §2 / OQ-20)
      Work Type     := "Learn"
      Revision Done := 0
      Paused At     := (unset)
    Create sub-task:
      Summary      := "[{{Stage}}][Learn] — {{summary}}"
      Labels       := ["learn"]
      Story Points := 2
      Due Date     := (unset)
  Else:
    // T6: resume revise chain at next index
    Set local var n := {{Revision Done}} + 1
    Set local var gap := lookup(n, 1=2, 2=5, 3=11, 4=25)   // business days
    Edit fields on current issue:
      Work Type := "Revise"
      Paused At := (unset)
    Create sub-task:
      Summary      := "[{{Stage}}][Revise#{{n}}] — {{summary}}"
      Labels       := ["revise"]
      Story Points := 1
      Due Date     := {{now.plusBusinessDays(gap)}}
```

(IM4 §13 Rule 3; IM5 §13 Rule 3.)

### 4.4 Rule 4 — Weekly Stale Scan (implements T9)

```
Trigger: Scheduled
  Cron: 0 10 ? * MON           // Monday 10:00 local
Filter (JQL): see §5 `Stale Eligible Units`
For each matched Parent issue:
  Create sub-task:
    Summary      := "[{{Stage}}][Test] — {{summary}}"
    Labels       := ["test"]
    Story Points := 2
    Due Date     := {{now.plusBusinessDays(2)}}   // RevisionGap[1]
  // Does NOT edit Last Worked At, Last Transitioned At, Revision Done, or any tuple field.
```

The **lifetime-idempotency guard** ([`LivingRequirements.md §5.2`](./LivingRequirements.md) T9, [`ImplementationTestMatrix.md §4`](./ImplementationTestMatrix.md) row E6) is enforced in the JQL, not in the rule body: the filter excludes any Parent whose history contains a `Test`-labelled Sub-task ever. See §5 below.

(IM4 §13 Rule 4; IM5 §13 Rule 4.)

---

## 5. JQL Filters and Saved Views

All queries assume Sub-task labels (`learn` / `revise` / `test`) follow §2. Filter names are aligned with the views defined in [`LivingRequirements.md §12`](./LivingRequirements.md).

| View ([`LivingRequirements.md §12`](./LivingRequirements.md)) | Saved-filter name | JQL |
|---|---|---|
| §12.1 Now/Due | `IP-Now` | `issuetype = Sub-task AND status in ("To Do", "In Progress") AND (duedate is EMPTY OR duedate <= 3d) ORDER BY duedate ASC, priority DESC` |
| §12.2 Current Working Set | `IP-Working-Set` | `issuetype != Sub-task AND "Lifecycle" = "Active" AND labels != "runner-system" ORDER BY "Last Worked At" DESC` |
| §12.3 Stale View | `IP-Stale` | `issuetype != Sub-task AND "Lifecycle" = "Active" AND "Last Worked At" <= -90d AND labels != "runner-system" ORDER BY "Last Worked At" ASC` |
| §12.4 Paused Queue (FIFO) | `IP-Paused-FIFO` | `issuetype != Sub-task AND "Lifecycle" = "Paused" AND labels != "runner-system" ORDER BY "Paused At" ASC` |
| §12.5 Archived | `IP-Archive` | `issuetype != Sub-task AND "Lifecycle" = "Archived" AND labels != "runner-system" ORDER BY updated DESC` |
| §12.6 Progress Velocity source | `IP-Velocity-LT` | `issuetype != Sub-task AND "Last Transitioned At" >= -30d AND labels != "runner-system" ORDER BY "Last Transitioned At" DESC` |

**System-artefact exclusion (`labels != "runner-system"`).** The External Runner ([`ExternalRunner.md §3`](./ExternalRunner.md)) stores its polling watermark in a dedicated Jira issue labelled `runner-system`. That issue shares the `Task`-derived parent-issue type with Unit-bearing issue types and would otherwise surface in `IP-Working-Set`, `IP-Stale`, `IP-Paused-FIFO`, `IP-Archive`, and `IP-Velocity-LT`. Every non-Sub-task filter above appends `AND labels != "runner-system"` to exclude it. `IP-Now` is already safe because it filters to `issuetype = Sub-task`. Deployments that do not use the External Runner can omit these clauses with no other impact; the External Runner's `python -m runner poll` bootstrap self-check will refuse to run until every affected filter has been amended ([`ExternalRunner.md §3.3`](./ExternalRunner.md)).

**T9 eligibility filter (used by Rule 4):**

Name: `IP-Stale-Eligible`

```
issuetype != Sub-task
AND "Lifecycle" = "Active"
AND "Last Worked At" <= -90d
AND labels != "runner-system"
AND issueFunction not in hasSubtasks("labels = test")
AND issueFunction not in hasSubtasks("status in (\"To Do\", \"In Progress\")")
```

Notes on the above:
- The first three clauses encode [`ImplementationTestMatrix.md §4`](./ImplementationTestMatrix.md) row E1 predicate `K ≠ Independent ∧ L = Active ∧ S = true`. `K ≠ Independent` is satisfied implicitly when Rule 4 runs only in `CORE-PREP` and `EXTENDED` projects (§1); Independent projects disable Rule 4.
- The `labels != "runner-system"` clause excludes the External Runner's System Config issue ([`ExternalRunner.md §3`](./ExternalRunner.md)); omit this clause on deployments that do not use the External Runner.
- The `hasSubtasks("labels = test")` clause encodes the **lifetime-idempotency guard** `H = false` ([`LivingRequirements.md §5.2`](./LivingRequirements.md) T9). It matches on any `Test`-labelled Sub-task, in any status (`To Do`, `In Progress`, `Done`, `Backlog`) and regardless of whether it was later deleted, as long as Jira retains its history. Implementations that prune Sub-task history must switch to a durable `Has Had Test` custom-field flag on the Parent, set by Rule 4 the moment it creates a Test Sub-task ([`LivingRequirements.md §5.2`](./LivingRequirements.md) T9 implementation note).
- The last clause encodes `O = false` (no outstanding actionable Sub-task).
- `issueFunction` comes from ScriptRunner; on vanilla Jira Cloud, use the smart-value equivalent inside the Rule 4 body instead of the JQL.

---

## 6. Board Configuration

One **Scrum board** per `Core` / `Extended` project; optional **Kanban board** for each `Independent` project.

### 6.0 Working Set = Active Sprint

The 10–15 day cycle described in [`LivingRequirements.md §6 FR1 / FR6`](./LivingRequirements.md) is implemented as a **Jira Sprint** on the Scrum board. Each cycle is a named Sprint (`Cycle 7 — DP patterns`, `Cycle 8 — Graphs`, etc.); the Unit issues the user wants to practise that cycle are added to the Sprint and their Sub-tasks follow the parent automatically. FR6's "one Working Set is active at a time" is enforced natively by Jira's single-active-sprint-per-board rule — no custom automation, no new data model required.

Lifecycle implications of the Sprint mapping:
- **Starting a cycle** — user creates a Sprint, drags Unit issues in, clicks "Start Sprint". This is a planning action, not a state-machine transition; `Unit.Lifecycle` is unaffected.
- **Closing a cycle** — user clicks "Complete Sprint". Jira natively prompts for the fate of incomplete issues (move to backlog / move to next sprint). This is also a planning action; Unit `Lifecycle` is untouched unless the user explicitly runs T7 (Archive) or T8 (Pause) on a Unit. A Unit may therefore stay `Active` across multiple Sprints — cycle membership and lifecycle intent are orthogonal.
- **Sprint membership is not a state-machine field.** No LSC / LT write fires when a Unit is added to or removed from a Sprint. The state machine is unaware of Sprints.
- **Historical Working Sets** — query with `sprint = "Cycle 7"` for retrospective views.

### 6.1 Core / Extended Scrum board (IM4 §11; IM5 §11)

- **Board filter:** `project = CORE-PREP AND issuetype in (Sub-task, Problem, Concept, Implementation, Pattern, Debug)` (so both layers appear but only Sub-tasks carry points).
- **Columns:** `Backlog`, `To Do`, `In Progress`, `Done` — i.e., Sub-task **Status**.
- **Swimlanes:** **Lifecycle** (options: `Active`, `Paused`, `Archived`). Rationale: Status (Columns) and Lifecycle (Swimlanes) are orthogonal axes ([`LivingRequirements.md §2`](./LivingRequirements.md)) — Status answers "what am I doing on this ticket right now?" and Lifecycle answers "will I invest more effort in this Unit at all?". Projecting them onto perpendicular axes of the board makes both visible at a glance; collapsing either into the other destroys a decision-support signal.
- **Alternate swimlane:** `Stage` (Beginner / Intermediate / Advanced) — selectable via a board-level toggle when the user wants stage-priority visibility instead of lifecycle visibility.
- **Quick filters:**
  - `Learn`: `labels = learn`
  - `Revise`: `labels = revise`
  - `Test`: `labels = test`
  - `Due this week`: `duedate <= 7d`
  - `Stale parents`: saved filter `IP-Stale`
- **Card layout:** show `Work Type` and `Revision Done / Revision Target` on Parent cards; show `Labels`, `Outcome` (Revise/Test only), and `Due Date` on Sub-task cards.

### 6.2 Independent Kanban board
- Board filter scoped to the Independent project key.
- Columns identical to Scrum board.
- Swimlanes **disabled** (no `Stage` axis — T5/T11 don't apply, [`LivingRequirements.md §5.3`](./LivingRequirements.md)).
- Quick filters: `Learn`, `Test`; no `Revise` filter (T2/T3/T4 don't fire on Independent Sets).

---

## 7. Knowledge-Storage Separation (IM5 §1.2)

IM5 §1.2 introduces an explicit split between the tracker and the canonical knowledge store, which aligns with Principle 2 in [`LivingRequirements.md §3`](./LivingRequirements.md) ("Separation of Concerns — artifacts hold raw working memory; canonical knowledge lives externally"):

| Layer | System | Content | Lifetime |
|---|---|---|---|
| **Execution / timeline** | Jira Parent + Sub-task | Raw notes, mistakes log, questions, attempt records, due dates | For the life of the Unit |
| **Canonical knowledge** | GitHub (or equivalent VCS) | Final, structured write-ups, code solutions, reference patterns | Permanent; decoupled from the Unit's Jira lifecycle |

Rule of thumb from IM5: if the content changes every attempt, it belongs in Jira; if it is the output the user wants to re-read in a year, it belongs in GitHub. The system **does not** enforce the promotion from Jira → GitHub; it is a user workflow, surfaced only through Principle 2 and reinforced by the lack of any `KnowledgeContent` field on the Unit.

**No URL custom field on the Unit.** The canonical repository is known by convention (the user's interview-prep GitHub repo); the Jira Unit issue holds execution artefacts whose role is to remind the user what to do next, not to point at the knowledge store. If a user ever needs a direct link to a specific GitHub write-up on a specific Unit, the ticket's `Description` field (free-form) and `Comments` stream (per [`LivingRequirements.md §6 FR9 / FR10`](./LivingRequirements.md)) are the channel.

A structured URL custom field was considered and rejected: it would add a field-edit cost to every Unit at the exact moment the user wants to move on, for a benefit the ticket body already provides at zero ceremony. The guiding heuristic is **the more lightweight and automated the system is, the more it will be followed** — every field the user has to remember to fill is a field that will eventually go empty, and a field that goes empty on high-value Units (the most-revised ones) is worse than no field at all because it gives the illusion of a tracked promotion pipeline without tracking one.

---

## 8. Deviations from IM5

Items where the current spec ([`LivingRequirements.md §§1–13`](./LivingRequirements.md)) has moved beyond or diverged from IM5's configuration choices. These are **not** bugs to fix in Jira; they are places where a literal IM5 implementation would under-serve the spec.

| # | IM5 behavior | Current spec | Implication for Jira |
|---|---|---|---|
| J1 | `Revision Target` uses per-parent override via issue editor | Same — IM5 supports it. `RevisionTarget` override is T10 ([`LivingRequirements.md §5.2`](./LivingRequirements.md)). | No change; just make the field user-editable. |
| J2 | Stale scan payload is a `Revise` Sub-task | Spec v0.6+ changed the payload to a `Test` Sub-task ([`LivingRequirements.md §10`](./LivingRequirements.md) D20, D26) | Implement Rule 4 with `labels = [test]` as written in §4.4. |
| J3 | Stale scan can re-fire on the same Parent indefinitely | Spec adds **lifetime idempotency** ([`LivingRequirements.md §5.2`](./LivingRequirements.md) T9; [`ImplementationTestMatrix.md §4`](./ImplementationTestMatrix.md) row E6) | The `IP-Stale-Eligible` JQL in §5 encodes the guard. Do not remove. |
| J4 | `Advanced` is described as "terminal — no upgrades beyond" | Spec ([`LivingRequirements.md §2`](./LivingRequirements.md)) treats all three stages as standard and makes only `Archived` terminal | **Resolved in [`LivingRequirements.md`](./LivingRequirements.md) v0.7.7 (OQ-20):** `next(Advanced) = Advanced` is now normative. T5 at Advanced resets the tuple to `(Advanced, Learn, Active, 0)` and spawns a fresh Learn Sub-task at Advanced — "restart Advanced". Rule 3's `next({{Stage}})` lookup therefore implements the three-entry table `Beginner → Intermediate`, `Intermediate → Advanced`, `Advanced → Advanced`. |
| J6 | IM5 has no regress path on failed revise or failed test | Spec v0.7.6+ introduces `Subtask.Outcome ∈ {Pass, Regress}` on Revise/Test Sub-tasks and two new transitions **T12** (Revise-Regress) / **T13** (Test-Regress) — [`LivingRequirements.md §3`](./LivingRequirements.md) Principle 7, [`§5.2`](./LivingRequirements.md), [`§5.5`](./LivingRequirements.md) | Requires the `Outcome` custom field (§2) and the Regress branches in Rule 2 (§4.2). Default `Outcome = Pass` preserves IM5-identical behaviour; Regress is opt-in and triggers a reset of `Revision Done` to 0 plus a fresh `Revise#1` Sub-task. T13 does **not** re-create T9 eligibility — `IP-Stale-Eligible` JQL (§5) is unchanged. |
| J5 | Knowledge split is a written convention, not tooled | Spec formalizes via Principle 2 in [`LivingRequirements.md §3`](./LivingRequirements.md) | No automation added; the split is a user-process concern. |
| J7 | Lifecycle field is edited by opening the issue and editing the custom-field picker | Same spec; same field. Team profile assumes custom-field-picker edit; Solo profile (§9) adds one-click Manual-Trigger buttons on the issue panel | See §9.1. No data-model change; Lifecycle remains the sole authority for the Active / Paused / Archived axis and Status remains the sole authority for To Do / In Progress / Done. The axes do **not** collapse. |
| J8 | T9 guard implemented via `issueFunction in hasSubtasks(...)` (ScriptRunner) | Spec allows either the hasSubtasks-style lookup **or** a durable `HasHadTest` flag ([`LivingRequirements.md §5.2`](./LivingRequirements.md) T9 implementation note; [`ImplementationTestMatrix.md §4`](./ImplementationTestMatrix.md) row E6) | Solo profile (§9.2) chooses the durable flag — one extra field edit per T9 firing; eliminates the ScriptRunner dependency entirely; works on free-tier Jira Cloud. |

---

## 9. Solo-User Profile (optional delta over §§1–8)

This section is an **additive, opt-in delta** over the team profile in §§1–8. It targets a single developer using the system as a personal decision-support tool on free-tier Jira Cloud (no ScriptRunner, no paid plugins). Every change here is additive: a team-profile implementation remains fully valid and no part of §§1–8 is overridden.

**Companion specification:** Jira Cloud Free's 100-runs-per-month Automation cap cannot host the full T1–T13 execution chain at the workload this profile targets. The canonical external-execution specification is [`ExternalRunner.md`](./ExternalRunner.md) (Posture J-C), which relocates Rules 1, 2, and 4 out of Jira Automation into a stateless GitHub Actions runner while preserving Rule 3 and the §9.1 Manual-Trigger buttons in Jira. The Solo profile defined here is the Jira-side contract that the External Runner consumes; the two specifications are intended to be read together. Deployments that do not use the External Runner can still adopt §9 on its own — every §9 change is Jira-local and independent of the execution substrate.

**Explicitly not in scope of the Solo profile:**
- No collapse of Status into Lifecycle (or vice versa). The two axes are orthogonal ([`LivingRequirements.md §2`](./LivingRequirements.md), §6.1): Lifecycle governs "do I expect to invest more effort in this Unit?", Status governs "what is my execution state on this specific ticket?". Columns = Status × Swimlanes = Lifecycle is preserved.
- No removal of custom fields. The `Outcome`, `Lifecycle`, `Work Type`, `Revision Done`, `Revision Target`, `Last Worked At`, `Last Transitioned At`, `Paused At`, and `Stage` custom fields all remain.
- No changes to transitions T1–T13. Rules 1–4 keep the same pre-states, post-states, and timestamp writes. The Solo profile only changes the *user-facing invocation surface* for user-initiated transitions and the *implementation* of the T9 guard.

### 9.1 Manual-Trigger automations for user-initiated Lifecycle transitions (T5 / T6 / T7 / T8)

Jira Cloud Automation natively supports **Manual Trigger** rules: a button appears on the issue view that, when clicked, runs a rule. This converts the Lifecycle custom-field edit from a multi-click interaction (open issue → locate field → select value → save) into a single click, without changing the data model.

Define four rules, scoped to the Unit issue types (`Problem`, `Concept`, `Implementation`, `Pattern`, `Debug`) in the `Core` / `Extended` / `Independent` projects:

| Rule name | Button label | Fires on | Actions | Underlying transition |
|---|---|---|---|---|
| `Solo-Archive` | **Archive** | Unit issues only | `Lifecycle := Archived` | T7 |
| `Solo-Pause` | **Pause** | Unit issues where `Lifecycle = Active` | `Lifecycle := Paused` · `Paused At := now` | T8 |
| `Solo-Resume` | **Resume** | Unit issues where `Lifecycle ∈ {Paused, Archived}` | `Lifecycle := Active` (Rule 3 in §4.3 then fires T5 or T6 deterministically on the `Lifecycle` field-change trigger) | T5 or T6 (chosen by Rule 3) |
| `Solo-Unarchive` | (merged into **Resume**) | — | — | — |

The Manual-Trigger rules are a **UI shortcut, not a new writer**. The normative writer of `Lifecycle` remains the Lifecycle custom field itself (per [`LivingRequirements.md §5.4`](./LivingRequirements.md) rule 2). Rule 3 (§4.3) continues to observe the field-change event and fire T5 / T6 as appropriate — the Manual-Trigger rule simply edits the field on the user's behalf.

**What the Solo profile does *not* do with these buttons:**
- Does not close the Jira ticket on Archive. `Status` is untouched; the Sub-tasks of an Archived Unit retain whatever Status they had.
- Does not move the ticket to Backlog on Pause. Again: Status is orthogonal.
- Does not write LSC or LT directly. T5 / T6 / T7 / T8 already specify their Timestamp Updates in [`LivingRequirements.md §5.2`](./LivingRequirements.md); Rule 3 performs those writes as today.

### 9.2 ScriptRunner-free T9 via durable `Has Had Test` flag

Replace the `issueFunction in hasSubtasks(...)` clause in §5's `IP-Stale-Eligible` JQL with a durable Boolean custom field, per the implementation note in [`LivingRequirements.md §5.2`](./LivingRequirements.md) T9 and [`ImplementationTestMatrix.md §4`](./ImplementationTestMatrix.md) row E6.

**New custom field:**

| Custom Field | Jira type | Issue types | Default | Writer |
|---|---|---|---|---|
| `Has Had Test` | Checkbox (Boolean) | `Problem`, `Concept`, `Implementation`, `Pattern`, `Debug` | `false` (unchecked) | Rule 4 only, at the moment it creates a Test Sub-task. Durable: never cleared (not by T5/T11 upgrade, not by T7 Archive, not by T13 Regress re-entry — per [`LivingRequirements.md §5.2`](./LivingRequirements.md) T13 note and [`ImplementationTestMatrix.md §4`](./ImplementationTestMatrix.md)). |

**Revised `IP-Stale-Eligible` JQL (Solo profile):**

```
issuetype != Sub-task
AND project in (CORE-PREP, EXTENDED)
AND "Lifecycle" = "Active"
AND "Last Worked At" <= -90d
AND "Has Had Test" = false
AND labels != "runner-system"
AND status not in (Done)
```

The last clause (`status not in (Done)`) is a best-effort substitute for the outstanding-Sub-task check (`O = false`) — on the Solo profile the user's board discipline provides the rest. A stricter substitute using only native Jira Cloud features:

```
AND issuetype != Sub-task
AND NOT (
  parent in (
    issuetype = Sub-task AND status in ("To Do", "In Progress")
  )
)
```

**Revised Rule 4 (Solo profile) — addition to the existing §4.4 pseudocode:**

```
Trigger: Scheduled
  Cron: 0 10 ? * MON
Filter (JQL): IP-Stale-Eligible   (Solo-profile version above)
For each matched Parent issue:
  Create sub-task:
    Summary      := "[{{Stage}}][Test] — {{summary}}"
    Labels       := ["test"]
    Story Points := 2
    Due Date     := {{now.plusBusinessDays(2)}}
  Edit fields on Parent issue:
    Has Had Test := true           // NEW in Solo profile
  // Does NOT edit Last Worked At, Last Transitioned At, Revision Done,
  // Work Type, Stage, Lifecycle, or any other tuple field. (Unchanged.)
```

The Solo-profile Rule 4 writes exactly one additional field relative to the Team profile. `Has Had Test` is set once per Unit lifetime and never cleared — the exact durable-one-bit-guard behaviour [`ImplementationTestMatrix.md §4`](./ImplementationTestMatrix.md) describes in its "H is durable" invariant.

### 9.3 Cognitive-load impact (before / after)

Audit §2.4 scorecard rows for a user on the Solo profile:

| Interaction | Team profile (§§1–8) | Solo profile (§9) |
|---|---|---|
| Pause a Unit mid-chain | Open issue · locate Lifecycle · select `Paused` · save | Click **Pause** button |
| Resume a Paused Unit | Open issue · locate Lifecycle · select `Active` · save | Click **Resume** button |
| Archive a Unit | Open issue · locate Lifecycle · select `Archived` · save | Click **Archive** button |
| Configure T9 scan on fresh Jira Cloud | Install ScriptRunner (or rewrite §5 JQL by hand) | Create `Has Had Test` field + use Solo-profile JQL (5 minutes, no plugins) |

The data model, the state machine, the board layout, and every invariant in [`LivingRequirements.md`](./LivingRequirements.md) and [`ImplementationTestMatrix.md`](./ImplementationTestMatrix.md) are untouched. All §9 does is swap the invocation surface for Lifecycle edits and the implementation of the T9 durability guard — two narrow, orthogonal improvements that together remove the bulk of the day-to-day friction the v0.7.6 audit identified.
