● Part B — Manual UI Configuration: step-by-step

  Everything below is UI-only on Jira Cloud Free (the Automation REST API isn't exposed on that tier). All field / button labels below are copy-paste-safe.

  You will do each section per project — that means once for COREPREP and once for EXTENDED, except §B.5 (Notification scheme) which is user-level and runs once total. Estimated total time: ~25 minutes.


  ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  §B.1 — Rule 3: Lifecycle field-change → T5 / T6 / T7 / T8 (per project)

  Rule 3 is the only Jira-side automation the runner relies on. It fires on every Lifecycle field change (whether manual or triggered by the three buttons in §B.2–B.4) and stamps the correct timestamps / audit comment.

  Navigation (repeat for COREPREP, then EXTENDED):

    1. Open the project → sidebar → Project settings → Automation → Create rule (top-right).
    2. Trigger → Field value changed → configure:
       • Fields to monitor: Lifecycle
       • Change type: Any changes to the field value
       • For: tick both Edit issue and Automation rule actions
       • Issue types: Task, Story, Epic (these are what exist on the Scrum template; the spec names Problem / Concept / Implementation / Pattern / Debug are project-specific and aren't present on your site — use the default Unit-level types)
       • Save component.
    3. New component → Add condition → Issue fields condition:
       • Field: Labels
       • Condition: does not contain
       • Value: runner-system
       • (This prevents Rule 3 from ever firing on COREPREP-1 / EXTENDED-1.)
       • Save.
    4. New component → Add branch → If / else block → pick Advanced compare conditions. You'll create four sibling branches, each with a matching set of actions.

     Branch T7 — `Archived`:
       • First value: smart-value {{issue.Lifecycle.name}} (or {{issue.Lifecycle}})
       • Condition: equals
       • Second value: Archived
       • Inside that branch, Add action → Edit issue → set Last Transitioned At = {{now}}.
       • Then Add action → Comment on issue → body: [Runner][T7] Archived via Lifecycle change.

     Branch T8 — `Paused`:
       • First value: {{issue.Lifecycle.name}}, equals, Second value: Paused
       • Edit issue → Paused At = {{now}}, Last Transitioned At = {{now}}
       • Comment on issue → [Runner][T8] Paused via Lifecycle change.

     Branch T5 — `Active` (was `Archived`):
       • First value: {{issue.Lifecycle.name}}, equals, Second value: Active
       • Nested condition → Advanced compare → {{changelog.Lifecycle.fromString}} equals Archived
       • Edit issue → Last Transitioned At = {{now}}, Last Worked At = {{now}}
       • Comment → [Runner][T5] Unarchived to Active.

     Branch T6 — `Active` (was `Paused`):
       • Same as T5 but nested condition is {{changelog.Lifecycle.fromString}} equals Paused
       • Edit issue → Last Transitioned At = {{now}}, Last Worked At = {{now}}
       • Comment → [Runner][T6] Resumed to Active.

    5. Rule details (top of the canvas): Name = Runner - Rule 3 - Lifecycle transitions. Leave actor as your own user.
    6. Turn on (top-right toggle). Save.

  Repeat for the EXTENDED project — Atlassian's UI has a "Copy rule" option under the rule's ... menu, which lets you duplicate to another project and adjust scope in one click.


  ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  §B.2 — Solo-Archive manual-trigger button (per project)

    1. Same nav: project → Project settings → Automation → Create rule.
    2. Trigger → Manual trigger from issue:
       • Issue types: Task, Story, Epic
       • Button label: Archive
    3. Condition → Issue fields condition:
       • Field: Lifecycle, Condition: is one of, Values: Active, Paused
    4. Action → Edit issue:
       • Lifecycle = Archived
       • (Rule 3 automatically picks up from here and writes T7 timestamps + audit comment.)
    5. Rule details → Name = Runner - Solo-Archive. Turn on. Save.
    6. Repeat for EXTENDED.

  §B.3 — Solo-Pause manual-trigger button (per project)

  Identical recipe, differ only in:
    • Button label: Pause
    • Condition: Lifecycle equals Active
    • Action: Lifecycle = Paused
    • Rule name: Runner - Solo-Pause

  §B.4 — Solo-Resume manual-trigger button (per project)

  Identical recipe:
    • Button label: Resume
    • Condition: Lifecycle is one of Paused, Archived
    • Action: Lifecycle = Active
    • Rule name: Runner - Solo-Resume

  After §B.1–§B.4 are done, each Unit's right-hand panel will show three buttons: Archive, Pause, Resume. Clicking any of them writes Lifecycle, which fires Rule 3, which writes the correct stamps and posts the audit comment.


  ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  §B.5 — Notification scheme (once, user-level)

  Prevents your inbox from exploding every time the runner writes a field.

    1. Top-right profile avatar → Personal settings → Notifications (or "Email notifications").
    2. Set:
       • Issue commented → ON
       • Issue assigned → ON
       • Issue created → ON
       • `Issue updated` (field edits) → OFF ← this is the important one
       • Issue resolved → ON
       • Issue closed → ON
    3. Save.


  ────────────────────────────────────────────────────────────────────────────────────────────────────────────

  §B.6 — Board swimlanes by Lifecycle (per project)

  The spec's "Status × Lifecycle matrix" (`JiraImplementation.md §6.1`) is realised as Kanban-style swimlanes.

    1. Open the project → Board (left sidebar → COREPREP board or EXTENDED board).
    2. Top-right `⋯` (board menu) → Configure board → Swimlanes tab.
    3. Base Swimlane on: Queries.
    4. Add three swimlanes:

     | Name | JQL query |
     |---|---|
     | Active | "Lifecycle" = "Active" |
     | Paused | "Lifecycle" = "Paused" |
     | Archived | "Lifecycle" = "Archived" |

    5. Leave the default "Everything else" swimlane empty — the three above cover the full Lifecycle enum.
    6. (Optional) Same board-menu → Card layout → add Work Type, Revision Done, Revision Target to parent cards; Labels, Outcome, Due Date to Sub-task cards.
    7. Repeat for the EXTENDED board.


  ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  Verification (Part E rows E7–E10 + E14)

  After the six sections above, run this spot-check:

  | # | Check | How | Expected |
  |---|---|---|---|
  | E7 | Rule 3 enabled per project | Project settings → Automation | Runner - Rule 3 - Lifecycle transitions shown toggled on |
  | E8 | 3 buttons visible on any Unit | Create a throwaway Unit → open it | Archive, Pause, Resume buttons render in the right panel |
  | E9 | Swimlanes match Lifecycle | Open the board | Three swimlanes labelled Active, Paused, Archived |
  | E10 | Sprint exists | Backlog view | Cycle 1 — bootstrap present (already created by the provisioner) |
  | E14 | Pause triggers T8 | Click Pause on a test Unit | Lifecycle flips to Paused; Paused At populated; [Runner][T8] audit comment appears within a few seconds |


  ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  Known risks to watch for during B.1 setup

    1. `{{issue.Lifecycle.name}}` vs `{{issue.Lifecycle}}` — select-list smart-values sometimes resolve to the option object and sometimes to the string, depending on Atlassian's renderer version. If the "Branch T7/T8/T5/T6" conditions don't match on the first try, switch between the two forms.
    2. Jira Cloud Free Automation limits — 100 executions/month on global rules, 500/month on single-project rules. All of ours are single-project → fine for a solo user.
    3. `Has Had Test` write from Rule 4 — out of scope for Part B, but flag: runner/rules.py:470 does await client.update_issue(unit_key, {"Has Had Test": True}). Since the field is modelled as a single-select with string options "true" / "false", Jira may reject the Python True value. If that fails during the
        M11 smoke test, the runner-side fix is to pass {"value": "true"} instead.


  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  When you're done with §B.1–§B.6 plus the E7–E10/E14 check, reply "Part B done" and I'll:
    1. Verify the three GitHub Secrets (JIRA_BASE_URL, JIRA_EMAIL, JIRA_TOKEN) are present on the repo — gh secret list.
    2. Seed the pilot Unit in COREPREP (Stage=Intermediate, Difficulty=Medium).
    3. Execute the M11 smoke dispatch: gh workflow run poll-dispatch.yml → gh run watch.
    4. Verify the T1 audit comment + watermark advance on COREPREP-1.
    5. Do the replay-safety dispatch for the M11 gate condition.
