● Option B it is. Condensed click-by-click — you already know the pattern from COREPREP, so this is just "do 3a from scratch in EXTENDED, then intra-copy three times."


  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  Rule 3a in EXTENDED — from scratch

  Step 1 — Navigate

    1. Top-left project switcher → `Extended` project.
    2. Left sidebar → Project settings → Automation.
    3. Top-right → `Create rule`.

  Step 2 — Trigger

    4. In the trigger list → scroll/search → click `Field value changed`.
    5. Form:
       • Fields to monitor for changes → Lifecycle.
       • Change type → Any changes to the field value (default).
       • For → All work item operations (default).
    6. `Next`.

  Step 3 — Condition 1 (label exclusion)

    7. `+ Add component` → `Work item fields condition`.
    8. Field Labels · Condition does not contain · Value runner-system · `Next`.

  Step 4 — Condition 2 (type scope)

    9. `+ Add component` → `Work item fields condition`.
    10. Field Work item Type · Condition is one of · Value chips: Task, Story, Epic · `Next`.

  Step 5 — Condition 3 (Lifecycle = Archived)

    11. `+ Add component` → `Work item fields condition`.
    12. Field Lifecycle · Condition equals · Value Archived · `Next`.

  Step 6 — Action 1 (stamp timestamp)

    13. `+ Add component` → `Edit work item` (in the "Then / Actions" section of the picker — scroll down past conditions).
    14. + Choose fields to set… → Last Transitioned At → value {{now}} → `Next`.

  Step 7 — Action 2 (audit comment)

    15. `+ Add component` → `Comment on work item`.
    16. Body:
      [Runner][T7] Archived via Lifecycle change.
    17. `Next`.

  Step 8 — Name + enable

    18. Top-right → `Rule details` → Name: Runner - Rule 3a - T7 Archived · Description: T7 lifecycle transition per ExternalRunner.md §4.3 / JiraImplementation.md §4.3 · close panel.
    19. `Turn on rule`.


  ──────────────────────────────────────────────────────────────────────────────────────────

  Rules 3b / 3c / 3d in EXTENDED — intra-project copy

  You're now on the Extended rules list with 3a live. Intra-project copy works on Free tier.

  Rule 3b — T8 Paused

    1. ⋯ on Runner - Rule 3a - T7 Archived → `Copy rule` → (dialog's Scope reads Extended, not Core Prep this time) → `Copy`.
    2. Open the (copy) rule → `Rule details` → Name → Runner - Rule 3b - T8 Paused.
    3. Edit the Lifecycle equals Archived condition → change Value to `Paused` → Next.
    4. Edit the Edit work item action → add second field: Paused At := {{now}} → Next.
    5. Edit the comment action → body:
     [Runner][T8] Paused via Lifecycle change.
     → Next.
    6. Turn on rule.

  Rule 3c — T5 Unarchived

    7. Back on rules list → ⋯ on Runner - Rule 3a - T7 Archived → Copy rule → Copy → open.
    8. Rename → Runner - Rule 3c - T5 Unarchived.
    9. Change Lifecycle equals Archived → Value Active → Next.
    10. Add smart-value condition between trigger and Edit action (drag up if it lands at bottom):
          • + Add component → `{{smart values}} condition`.
          • First value: {{changelog.Lifecycle.fromString}} (fallback: {{changelog.customfield_10137.fromString}})
          • Condition: equals
          • Second value: Archived
          • Next.
    11. Edit action → add second field: Last Worked At := {{now}} → Next.
    12. Comment body:
      [Runner][T5] Unarchived to Active.
      → Next.
    13. Turn on rule.

  Rule 3d — T6 Resumed

    14. Back on rules list → ⋯ on Runner - Rule 3c - T5 Unarchived → Copy rule → Copy → open.
    15. Rename → Runner - Rule 3d - T6 Resumed.
    16. Edit the smart-value condition → Second value: change Archived → `Paused` → Next.
    17. Comment body:
      [Runner][T6] Resumed to Active.
      → Next.
    18. Turn on rule.


  ───────────────────────────────────────────────────────────────────

  Checkpoint

  Extended → Project settings → Automation should show (all ENABLED):

  | Status | Rule |
  |---|---|
  | 🟢 | Runner - Rule 3a - T7 Archived |
  | 🟢 | Runner - Rule 3b - T8 Paused |
  | 🟢 | Runner - Rule 3c - T5 Unarchived |
  | 🟢 | Runner - Rule 3d - T6 Resumed |

  Reply "EXTENDED 3a–3d enabled" and we move to §B.2–B.4 (Archive / Pause / Resume manual-trigger buttons).


   Button architecture recap

  Each button is a tiny Automation rule with:

    • Trigger: Manual trigger from work item (Atlassian's new name for what the spec calls "Manual trigger from issue" — it renders as a clickable button in the issue panel)
    • Condition: Lifecycle is in a specific state (prevents invalid clicks)
    • Action: Edit work item → set Lifecycle to the target value
    • Side effects: none in this rule — Rule 3a/b/c/d (already enabled) fires T5/T6/T7/T8 automatically on the resulting Lifecycle field-change.

  Net effect: Pause button → sets Lifecycle := Paused → Rule 3b fires → writes Paused At + Last Transitioned At + [Runner][T8] comment.


  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

  §B.2 — Archive button (sets Lifecycle := Archived)

  In COREPREP → Project settings → Automation → Create rule

    1. Trigger → search/pick `Manual trigger from work item`.
       • Button label → Archive
       • Work item types → Task, Story, Epic
       • (No "Group" needed. Leave "Require confirmation" unchecked unless you want a confirm dialog.)
       • `Next`.
    2. Component → `Work item fields condition`.
       • Field Lifecycle · Condition is one of · Values Active, Paused
       • (Prevents clicking `Archive` on an already-Archived Unit.)
       • `Next`.
    3. Component → `Edit work item`.
       • + Choose fields to set… → Lifecycle → Value Archived
       • `Next`.
    4. `Rule details` top-right →
       • Name: Runner - Solo-Archive (manual trigger)
       • Description: §9.1 manual-trigger button — sets Lifecycle := Archived; Rule 3a fires T7
       • close panel.
    5. `Turn on rule`.


  ────────────────────────────────────────────────────────────

  §B.3 — Pause button (sets Lifecycle := Paused)

  Fastest path: intra-project copy the Archive rule and tweak.

  Still in COREPREP → Automation list

    6. ⋯ on Runner - Solo-Archive (manual trigger) → `Copy rule` → Copy → open the (copy).
    7. `Rule details` → Name: Runner - Solo-Pause (manual trigger) → close.
    8. Click the trigger card (Manual trigger from work item) → change Button label to `Pause` → Next.
    9. Click the condition card (Work item fields condition) → change to:
       • Field Lifecycle · Condition equals · Value Active
       • (Only allow Pause from Active. Paused/Archived shouldn't be re-paused.)
       • `Next`.
    10. Click the action card (Edit work item) → change Lifecycle value from Archived to `Paused` → Next.
    11. `Turn on rule`.


  ───────────────────────────────────────────────────────────────────────────────────────────────────────

  §B.4 — Resume button (sets Lifecycle := Active)

  Still in COREPREP → Automation list

    12. ⋯ on Runner - Solo-Archive (manual trigger) → `Copy rule` → Copy → open.
    13. `Rule details` → Name: Runner - Solo-Resume (manual trigger) → close.
    14. Trigger card → Button label → `Resume` → Next.
    15. Condition card →
          • Field Lifecycle · Condition is one of · Values Paused, Archived
          • (Cannot resume something already Active.)
          • `Next`.
    16. Action card → Lifecycle value → `Active` → Next.
    17. `Turn on rule`.


  ──────────────────────────────────────────────────────

  Replicate in EXTENDED

  Same manual pattern as Rule 3 (no cross-project copy on Free). Three rules, ~2 minutes each if you keep this tab open as a reference:

    18. Switch to Extended → Project settings → Automation → Create rule — rebuild Solo-Archive per steps 1–5 above.
    19. Intra-project copy it twice within EXTENDED, adjusting per steps 6–11 (Pause) and 12–17 (Resume).
1.
