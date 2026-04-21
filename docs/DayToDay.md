# Day-to-Day Workflow

This guide walks through the Unit lifecycle from the operator's point of
view, using the three Units that seeded the Sprint 75 validation on
`COREPREP`:

| Parent | Type | Stage | Difficulty | Summary |
|---|---|---|---|---|
| `COREPREP-3` | Problem | Beginner | Easy | `1D DP - Climbing Stairs and House Robber` |
| `COREPREP-4` | Concept | Beginner | Easy | `Java Basics - JDK/JRE/JVM and OOP Concepts` |
| `COREPREP-5` | Implementation | Beginner | Medium | `How to Design a Rate Limiter` |

What happens automatically — without any further operator action — is
documented below.

## 1. Creating a Unit

Open the `COREPREP` board and click **Create**. The runner considers any
issue whose type is one of `Problem`, `Concept`, `Implementation`,
`Pattern`, or `Debug` to be a Unit (`UNIT_ISSUE_TYPES` in
`runner/rules.py`). Set at minimum:

- **Stage** — `Beginner`, `Intermediate`, or `Advanced`.
- **Difficulty** — `Easy`, `Medium`, or `Hard` (optional; missing
  Difficulty falls back to `RevisionTarget = 2`).

No labels are needed; no Story Points either. Do not add the
`runner-system` label — that tag is reserved for the System Config
issue.

## 2. Rule 1 fires — T1 synthesis

The `poll-dispatch.yml` workflow runs every five minutes. On Jira Cloud
Free the `issue_created` changelog entry is omitted, so the runner
synthesises one: for any issue whose `created` timestamp is newer than
the watermark and whose changelog has no real creation entry,
`runner/ingestor.py` mints

```python
ChangelogEvent(id=0, issue_key=..., is_new_issue=True, ...)
```

Rule 1 then executes for each synthetic event:

1. `GET /rest/api/3/issue/{key}` reads the Unit.
2. `Stage` is read (required; pre-state violation otherwise).
3. `Difficulty` is mapped through
   `RevisionTarget = {"Easy": 2, "Medium": 3, "Hard": 4}`; missing keys
   fall back to `RevisionTargetDefault = 2` and an `_FALLBACK_NOTE`
   addendum is appended to the audit comment.
4. The idempotency key is
   `sha256(f"{issue_key}|{event_id}|T1")[:12]`.
5. A Sub-task is created under the Unit with
   `summary = f"[{stage}][Learn] — {unit_summary}"`,
   `labels = ["learn", f"idem:{hex}"]`, and `story_points = 2`.
6. `Revision Target` is written back on the parent Unit.
7. A `[Runner][T1]` audit comment is posted on the parent.

Sprint 75 observed output:

| Unit | Sub-task created | Sub-task summary | Revision Target | Idem key |
|---|---|---|---|---|
| `COREPREP-3` | `COREPREP-7` | `[Beginner][Learn] — 1D DP - Climbing Stairs and House Robber` | `2.0` | `idem_236efc3e1f9e` |
| `COREPREP-4` | `COREPREP-8` | `[Beginner][Learn] — Java Basics - JDK/JRE/JVM and OOP Concepts` | `2.0` | `idem_b4b6d2100fe7` |
| `COREPREP-5` | `COREPREP-9` | `[Beginner][Learn] — How to Design a Rate Limiter` | `3.0` | `idem_7b2ae40350d5` |

Each parent Unit now carries one audit comment of the form

```
[Runner][T1] Create(Beginner) → Learn#1
  RevisionDone: 0 → 0 (target 2)
  run: <run_id> · event: 0 · key: idem_236efc3e1f9e
```

(`COREPREP-5`'s target reads `3` because Medium Difficulty resolves to
`RevisionTarget = 3`.)

## 3. Story Points defaults

The numeric defaults live in `runner/rules.py::default_story_points`:

```python
_DEFAULT_STORY_POINTS = {"learn": 2, "revise": 1, "test": 2}
```

Rule 1 writes `2` on every Learn Sub-task; Rule 2 writes `1` on every
`Revise#k` it spawns; Rule 4 writes `2` on the stale-scan Test
Sub-task. Changing these numbers is a one-line edit in `rules.py`.

## 4. Story Points best-effort fallback

Some tenants hide the built-in `Story Points` field on the Sub-task
screen scheme. When Rule 1 tries to set it, Jira rejects the POST with
`400 Bad Request` and a per-field error identifying the Story Points
custom field ID.

`JiraClient.create_subtask` catches the 400, confirms the error
targets Story Points, drops that key from the payload, and retries the
POST. The Sub-task is created without Story Points and the runner logs

```json
{"lvl": "WARNING", "msg": "story_points_unsettable_fallback",
 "parent_key": "COREPREP-3", "story_points": 2}
```

Rule 1 continues normally: `Revision Target` is still seeded, the
audit comment is still posted, and the idempotency label is still
written. Velocity analytics degrade (no SP recorded on the Sub-task)
until the tenant's field-configuration scheme exposes Story Points on
the Sub-task create screen. Running
`python -m scripts.provision_jira --update-filters` against an existing
tenant also attaches the runner-owned Story Points field to every
project screen via `_attach_runner_fields_to_screen`.

The Sprint 75 tenant emitted one such WARNING per Unit — expected on
that Jira Cloud Free configuration.

## 5. Visibility — `labels IS EMPTY`

The six non-`IP-Now` saved filters exclude the System Config issue
with

```jql
AND (labels IS EMPTY OR labels != "runner-system")
```

The `IS EMPTY` disjunct is necessary because bare
`labels != "runner-system"` drops any issue whose `labels` field is
null. Freshly-created Units like `COREPREP-3/-4/-5` have no labels at
all, so without the disjunct they would be invisible to
`IP-Working-Set`, `IP-Stale`, `IP-Paused-FIFO`, `IP-Archive`,
`IP-Velocity-LT`, and `IP-Stale-Eligible`.

The runner's own poll JQL (`runner/cli.py::_jql_updated_since`) uses
the same clause. If you provisioned the filters before v0.7.9, run

```bash
python -m scripts.provision_jira --update-filters
```

to rewrite them in place.

## 6. Marking a Learn Sub-task Done

Drag `COREPREP-7` to the **Done** column. Jira emits a
`status → Done` changelog event; the next poll invokes Rule 2
(`rule2_subtask_done`), which reads the parent tuple
`(Stage, WorkType, Lifecycle, Outcome)` and dispatches through the
pure state machine.

For a Learn Sub-task whose parent carries
`WorkType = Learn, Lifecycle = Active, Outcome = Pass`, the dispatcher
selects `T2`. Rule 2 then:

1. Writes `Last Subtask Completed At = <event.created>` on the parent
   (unconditional — fires even when dispatch is a NOOP).
2. Writes `Work Type = Revise` and `Last Transitioned At = now`.
3. Creates `Revise#1` Sub-task with
   `summary = "[Beginner][Revise#1] — 1D DP - Climbing Stairs and House Robber"`,
   `labels = ["revise", f"idem:{hex}"]`,
   `story_points = 1`,
   `duedate = now + RevisionGap[0] = +2 business days`.
4. Posts a `[Runner][T2]` audit comment:

```
[Runner][T2] Learn → Revise#1
  RevisionDone: 0 → 0 (target 2)
  Outcome: Pass
  DueDate(Revise#1): 2026-04-25  (RevisionGap[0] = 2bd)
  run: <run_id> · event: <event_id> · key: idem_<hex>
```

### Subsequent Revise Sub-tasks

When `Revise#1` is marked Done with Pass and `Revision Done + 1 <
Revision Target`, Rule 2 dispatches `T3`:

- `Revision Done` increments.
- `Revise#(k+1)` is created with due date
  `now + RevisionGap[k]` business days.
- `RevisionGap = [2, 5, 11, 25]` — the gap between Revise#1 and
  Revise#2 is 5 business days, Revise#3 lands 11 business days after
  Revise#2, and so on.

### Reaching the Revision Target

When `Revision Done + 1 == Revision Target` at Pass, Rule 2
dispatches `T4`: no successor Sub-task is spawned, `Lifecycle`
flips to `Paused`, and `Paused At = now` is recorded. For
`COREPREP-3` (target 2) this happens after `Revise#2` is marked Done.

### Regress paths

- `T12` — a Revise Sub-task is marked Done with `Outcome = Regress`.
  `Revision Done` resets to `0`; a new `Revise#1` is spawned.
- `T13` — a Test Sub-task is marked Done with `Outcome = Regress`.
  The Unit re-enters Learn: `Work Type = Learn`, `Revision Done = 0`,
  and a new Learn Sub-task is created.

## 7. Rule 4 — stale scan (T9)

`stale-scan.yml` runs every Monday at 10:00 UTC. It queries the
`IP-Stale-Eligible` saved filter:

```jql
issuetype != Sub-task AND project in (COREPREP, EXTENDED)
  AND "Lifecycle" = "Active" AND "Last Worked At" <= -90d
  AND "Has Had Test" = "false"
  AND (labels IS EMPTY OR labels != "runner-system")
  AND status not in (Done)
```

For each match, Rule 4:

1. Creates a `[Stage][Test] — <unit_summary>` Sub-task due
   `+RevisionGap[0] = +2 business days`, with `story_points = 2` and
   `labels = ["test", f"idem:{hex}"]`.
2. Writes `Has Had Test = true` on the parent — a set-once flag that
   permanently removes the Unit from the stale-scan pool.
3. Posts a `[Runner][T9]` audit comment.

Rule 4 does not touch `Revision Done`, `Lifecycle`, or
`Revision Target`. It fires at most once per Unit (the `Has Had Test`
guard) and does not advance the state machine on its own.

## 8. What to check on a normal day

- **`IP-Now`** — Sub-tasks due today or overdue.
- **`IP-Working-Set`** — Active Units sorted by `Last Worked At`.
- **`IP-Stale`** — Active Units idle 90+ days (candidates for Rule 4).
- **`IP-Paused-FIFO`** — Units that hit their revision target; revisit
  in FIFO order by `Paused At`.
- **`IP-Velocity-LT`** — the 30-day completion window for velocity
  analytics.

The runner's System Config issue (label `runner-system`) is never
visible in any of these. If it appears, the mandatory exclusion clause
is missing from the saved filter — rerun
`python -m scripts.provision_jira --update-filters` or patch the
filter in the Jira UI to restore it.

## 9. Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| Fresh Unit missing from `IP-Working-Set` | Saved filter still uses bare `labels != "runner-system"` | `python -m scripts.provision_jira --update-filters` |
| `story_points_unsettable_fallback` WARNING on every Rule 1 run | Story Points hidden on Sub-task screen scheme | Rerun the provisioner to attach the runner-owned Story Points field, or edit the tenant screen scheme |
| Rule 1 never fires on new Units | Watermark ahead of `created` timestamp, or `Stage` unset | Check `Last Successful Poll At` on the System Config issue; verify `Stage` on the Unit |
| Duplicate Sub-tasks after a retry | Idempotency label `idem:<hex>` missing on the prior Sub-task | Inspect the Sub-task labels; if the label was stripped manually, the next event will re-create the Sub-task — expected |
| Audit comment missing but Sub-task present | Prior run crashed between the write and the POST | Next poll re-posts the comment via `audit.comment_exists` |

## 10. Further reading

- `docs/ExternalRunner.md` — full rule specifications and state machine.
- `docs/LivingRequirements.md` — domain model and T1–T13 semantics.
- `docs/JiraProvisioningGuide.md` — operator setup checklist.
- `docs/DeveloperGuide.md` — local CLI reference.
