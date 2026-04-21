# Jira Stateless Runner Provisioning Guide — Posture J-C

> **Status:** Operational reference · v0.1.2 · **Last updated:** 2026-04-19 · **Source of truth:** [`LivingRequirements.md`](./LivingRequirements.md) v0.7.8, [`JiraImplementation.md`](./JiraImplementation.md) v0.7.9, [`ExternalRunner.md`](./ExternalRunner.md) v0.1.1

This guide walks a solo developer through the one-time provisioning of a Jira Cloud Free site as the **State Substrate** of the Posture J-C architecture. It is an operational companion to [`ExternalRunner.md`](./ExternalRunner.md) §3 (State Management) and [`JiraImplementation.md`](./JiraImplementation.md) §§1–9 (data model and Solo-User Profile).

**Authority rule:** this guide adds no requirements. If any instruction below conflicts with [`LivingRequirements.md`](./LivingRequirements.md), [`JiraImplementation.md`](./JiraImplementation.md), or [`ExternalRunner.md`](./ExternalRunner.md), those documents win and this guide is stale.

**Stateless-Runner principle (recap):** Jira holds **all** authoritative state — custom fields on Units, the Status × Lifecycle board, the Sprint-as-Working-Set mapping, and the System Config issue's polling watermark. The External Runner on GitHub Actions is a pure processor — it reads events from Jira, executes T1–T13 per [`LivingRequirements.md §5.2`](./LivingRequirements.md), and writes the resulting state changes back. A fresh `git clone` of the runner on a new machine resumes operation with zero data loss because **nothing operational lives outside Jira**.

**Scope split:**

| Phase | What | Substrate |
|---|---|---|
| Part 0 | Prerequisites + API token | Atlassian account |
| **Part A** | **API-automated provisioning** — projects, fields (+ options, screen attachments, defaults), System Config issue, 7 saved filters, boards, sprints | Jira REST API |
| **Part B** | **Manual UI configuration** — Rule 3 (Lifecycle field-change → T5–T8) + §9.1 Manual-Trigger buttons (Archive / Pause / Resume), notification scheme, board swimlanes | Jira UI (Free-tier Automation is UI-only) |
| Part C | GitHub Secrets wiring for the External Runner | GitHub Actions |
| Part D | Smoke test — first `workflow_dispatch` run | End-to-end |
| Part E | Verification checklist | — |
| **Part F** | **Daily operational workflow** — manual user actions (Jira UI) vs automated runner logic (GitHub Actions); single-Unit lifecycle walkthrough demonstrating the Stateless-Runner principle | Jira UI + GitHub Actions |

---

## Part 0 — Prerequisites & API Token Generation

### 0.1 Atlassian account requirements

| Requirement | Why | How to check |
|---|---|---|
| Atlassian Cloud site with Jira Software enabled | Hosts all state | `https://<yoursite>.atlassian.net/jira/your-work` loads |
| Site-admin role on the account used for provisioning | Required for creating custom fields and projects via REST | Jira → ⚙ → Atlassian admin → your profile shows "Site admin" |
| Jira Cloud **Free** plan (sufficient) | Posture J-C is designed against Free-tier limits | ⚙ → Billing shows "Free" |
| Jira Software product enabled | Scrum boards + sprints | ⚙ → Products shows "Jira Software" |

**If site-admin is not available:** project-admin alone is sufficient for issue/filter/sprint creation, but custom-field creation requires site-admin. Without it, Part A.2 (field provisioning) must be performed in the UI by a site-admin; the rest remains API-automatable.

### 0.2 Generate an Atlassian API token

The token is the password-equivalent used by `Authorization: Basic <base64(email:token)>` against the Jira REST API. It is also the value that will be stored as the `JIRA_TOKEN` GitHub Secret for the External Runner.

**Steps:**

1. Open `https://id.atlassian.com/manage-profile/security/api-tokens` in a browser signed in with your Atlassian account.
2. Click **Create API token**.
3. Label it `jira-stateless-runner` (this label is for your reference only; Atlassian tokens are account-scoped, not project-scoped).
4. Copy the token **immediately** — Atlassian shows it exactly once.
5. Record the three values you will need downstream:
   - `JIRA_BASE_URL` — e.g., `https://akverma.atlassian.net` (no trailing slash)
   - `JIRA_EMAIL` — your Atlassian account email
   - `JIRA_TOKEN` — the token copied above

### 0.3 Verify the token against the Jira REST API

```bash
curl -s -u "$JIRA_EMAIL:$JIRA_TOKEN" \
  -H "Accept: application/json" \
  "$JIRA_BASE_URL/rest/api/3/myself" | jq .accountId
```

A successful response prints your Atlassian account ID. A `401` indicates the email/token pair is wrong; a `403` indicates insufficient scope.

### 0.4 Record your accountId

```bash
export JIRA_ACCOUNT_ID="$(curl -s -u "$JIRA_EMAIL:$JIRA_TOKEN" \
  "$JIRA_BASE_URL/rest/api/3/myself" | jq -r .accountId)"
echo "$JIRA_ACCOUNT_ID"
```

This value is reused as `leadAccountId` when creating projects in Part A.1.

### 0.5 Credential-handling mandate (from [`ExternalRunner.md §8.2`](./ExternalRunner.md))

| Rule | Applies to |
|---|---|
| `JIRA_TOKEN` MUST NEVER be committed to any repository (encrypted or not) | All repos |
| `JIRA_TOKEN` MUST be stored only as a GitHub Secret at repo scope (Part C) | `jira-stateless-runner` repo |
| `JIRA_TOKEN` MUST NOT appear in log output, audit comments, or artefacts | Runner logs, Jira comments |
| `JIRA_TOKEN` SHOULD be rotated quarterly, or immediately on suspected compromise | Atlassian account settings |

A `.gitleaks.toml` pre-commit hook in the runner repo enforces the first rule automatically.


## Part A — API-Automated Provisioning

Every step in this part is executable via the Jira REST v3 API using the token generated in §0.2. Commands are shown as `curl` invocations for copy-paste portability; a scripted runner can substitute an HTTP client of choice.

**Common header shorthand:**

```bash
AUTH=(-u "$JIRA_EMAIL:$JIRA_TOKEN")
HDR=(-H "Accept: application/json" -H "Content-Type: application/json")
```

### A.1 Create projects (`CORE-PREP`, `EXTENDED`)

Per [`JiraImplementation.md §1`](./JiraImplementation.md), Sets map to Jira **projects**. `Core` and `Extended` use the Scrum template; `Independent-*` projects use the Kanban template and are created on-demand later.

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/project" -d '{
  "key": "CORE-PREP",
  "name": "Core Prep",
  "projectTypeKey": "software",
  "projectTemplateKey": "com.pyxis.greenhopper.jira:gh-simplified-scrum-classic",
  "leadAccountId": "'"$JIRA_ACCOUNT_ID"'",
  "assigneeType": "PROJECT_LEAD"
}'

curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/project" -d '{
  "key": "EXTENDED",
  "name": "Extended Prep",
  "projectTypeKey": "software",
  "projectTemplateKey": "com.pyxis.greenhopper.jira:gh-simplified-scrum-classic",
  "leadAccountId": "'"$JIRA_ACCOUNT_ID"'",
  "assigneeType": "PROJECT_LEAD"
}'
```

**Independent projects** (deferred until needed):

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/project" -d '{
  "key": "GOOGLE150",
  "name": "Google 150",
  "projectTypeKey": "software",
  "projectTemplateKey": "com.pyxis.greenhopper.jira:gh-simplified-kanban-classic",
  "leadAccountId": "'"$JIRA_ACCOUNT_ID"'"
}'
```

### A.2 Provision custom fields

Custom fields are site-scoped and visible to all projects once created. The full field set per [`JiraImplementation.md §2`](./JiraImplementation.md) plus [`ExternalRunner.md §3.2`](./ExternalRunner.md):

| # | Field name | Type (`type` value in API) | Applied to |
|---|---|---|---|
| 1 | `Stage` | `com.atlassian.jira.plugin.system.customfieldtypes:select` (options: `Beginner`, `Intermediate`, `Advanced`) | Unit types |
| 2 | `Work Type` | `select` (options: `Learn`, `Revise`) | Unit types |
| 3 | `Lifecycle` | `select` (options: `Active`, `Paused`, `Archived`; default `Active`) | Unit types |
| 4 | `Difficulty` | `select` (options: `Easy`, `Medium`, `Hard`) | Unit types (optional — fallback default=Target 2) |
| 5 | `Revision Target` | `float` (integer-valued) | Unit types |
| 6 | `Revision Done` | `float` (default 0) | Unit types |
| 7 | `Outcome` | `select` (options: `Pass`, `Regress`; default `Pass`) | Sub-task |
| 8 | `Has Had Test` | `checkbox` (boolean; default `false`) | Unit types |
| 9 | `Last Worked At` | `datetime` | Unit types |
| 10 | `Last Transitioned At` | `datetime` | Unit types |
| 11 | `Paused At` | `datetime` | Unit types |
| 12 | `Last Processed Changelog Id` | `float` (integer-valued) | `Task` (System Config only) |
| 13 | `Last Successful Poll At` | `datetime` | `Task` (System Config only) |
| 14 | `Last Stale Scan At` | `datetime` | `Task` (System Config only) |
| 15 | `Runner Version` | `textfield` (short) | `Task` (System Config only) |
| 16 | `Open Alert Issue Url` | `url` | `Task` (System Config only) |

**Example — create the `Stage` single-select field:**

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/field" -d '{
  "name": "Stage",
  "type": "com.atlassian.jira.plugin.system.customfieldtypes:select",
  "searcherKey": "com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher"
}'
```

The response contains the `id` (e.g. `customfield_10042`). Repeat for all 16 fields. Add options to select-type fields via `POST /rest/api/3/field/{fieldId}/context/{contextId}/option`.

### A.3 Attach fields to screens / issue types

Attach Unit-level fields (#1–11) to the **Problem / Concept / Implementation / Pattern / Debug** issue types' default screen, and System Config fields (#12–16) to the `Task` type on the System Config screen only. The canonical sequence is `GET /rest/api/3/issuetypescreenscheme/project → /issuetypescreenscheme/{id}/mapping → /screenscheme?id=… → POST /rest/api/3/screens/{id}/tabs/{tabId}/fields`. Record `customfield_*` IDs — the External Runner references fields by **name** via its `jira_client.py` resolver, so IDs are opaque.

**Automated:** `scripts/provision_jira.py :: ensure_field_screen_attachments` resolves the ISTS → ScreenScheme → Screen chain per project and idempotently attaches all 16 Runner fields to the first tab of every screen in that chain. Reruns are no-ops once fields are in place.

**Field defaults** (per §A.2 "default `Active`", "default `Pass`", "default 0", "default `false`") are applied by `ensure_field_defaults` via `PUT /rest/api/3/field/{fid}/context/defaultValue` after screen attachment. The three select-list defaults (`Lifecycle`, `Outcome`, `Has Had Test`) are mandatory; the `Revision Done=0` numeric default is best-effort and a `defaults_skipped` warning is emitted on Free-tier rejection.

### A.4 Create the System Config issue (one per project)

Per [`ExternalRunner.md §3.2`](./ExternalRunner.md), the System Config issue hosts the polling watermark and health timestamps. It is labelled `runner-system` so the v0.7.9 filters exclude it.

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/issue" -d '{
  "fields": {
    "project":   { "key": "CORE-PREP" },
    "issuetype": { "name": "Task" },
    "summary":   "Runner System Config — CORE-PREP",
    "labels":    ["runner-system", "hidden"],
    "description": {
      "type": "doc", "version": 1,
      "content": [{ "type": "paragraph", "content": [{
        "type": "text",
        "text": "DO NOT EDIT MANUALLY. This issue is the External Runner state substrate. See Augment/ProjectManagement/ExternalRunner.md §3."
      }]}]
    }
  }
}'
```

**Initial values are intentionally empty** — the runner treats absent `Last Processed Changelog Id` as `0` (start-of-time) and writes the first watermark on its first successful poll. Repeat once per project (`CORE-PREP`, `EXTENDED`, each `Independent-*`).

### A.5 Create the seven mandatory saved JQL filters (v0.7.9)

Filters are site-scoped; create once. Every non-Sub-task filter below includes the mandatory `AND (labels IS EMPTY OR labels != "runner-system")` exclusion clause per [`JiraImplementation.md §5`](./JiraImplementation.md) v0.7.9 and [`ExternalRunner.md §3.3`](./ExternalRunner.md). The `IS EMPTY` disjunct keeps freshly-created Units (which have no labels yet) visible in the view.

**Filter 1 — `IP-Now` (safe; no exclusion needed):**

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/filter" -d '{
  "name": "IP-Now",
  "description": "Now / Due — actionable Subtasks (LivingRequirements.md §12.1)",
  "jql": "issuetype = Sub-task AND status in (\"To Do\", \"In Progress\") AND (duedate is EMPTY OR duedate <= 3d) ORDER BY duedate ASC, priority DESC"
}'
```

**Filter 2 — `IP-Working-Set`:**

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/filter" -d '{
  "name": "IP-Working-Set",
  "description": "Current Working Set — Active Units (LivingRequirements.md §12.2)",
  "jql": "issuetype != Sub-task AND \"Lifecycle\" = \"Active\" AND (labels IS EMPTY OR labels != \"runner-system\") ORDER BY \"Last Worked At\" DESC"
}'
```

**Filter 3 — `IP-Stale`:**

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/filter" -d '{
  "name": "IP-Stale",
  "description": "Stale Active Units — 90d idle (LivingRequirements.md §12.3)",
  "jql": "issuetype != Sub-task AND \"Lifecycle\" = \"Active\" AND \"Last Worked At\" <= -90d AND (labels IS EMPTY OR labels != \"runner-system\") ORDER BY \"Last Worked At\" ASC"
}'
```

**Filter 4 — `IP-Paused-FIFO`:**

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/filter" -d '{
  "name": "IP-Paused-FIFO",
  "description": "Paused queue — FIFO by Paused At (LivingRequirements.md §12.4)",
  "jql": "issuetype != Sub-task AND \"Lifecycle\" = \"Paused\" AND (labels IS EMPTY OR labels != \"runner-system\") ORDER BY \"Paused At\" ASC"
}'
```

**Filter 5 — `IP-Archive`:**

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/filter" -d '{
  "name": "IP-Archive",
  "description": "Archived Units (LivingRequirements.md §12.5)",
  "jql": "issuetype != Sub-task AND \"Lifecycle\" = \"Archived\" AND (labels IS EMPTY OR labels != \"runner-system\") ORDER BY updated DESC"
}'
```

**Filter 6 — `IP-Velocity-LT`:**

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/filter" -d '{
  "name": "IP-Velocity-LT",
  "description": "Progress Velocity source — 30-day Last Transitioned At (LivingRequirements.md §12.6)",
  "jql": "issuetype != Sub-task AND \"Last Transitioned At\" >= -30d AND (labels IS EMPTY OR labels != \"runner-system\") ORDER BY \"Last Transitioned At\" DESC"
}'
```

**Filter 7 — `IP-Stale-Eligible` (Solo profile, used by Rule 4 / T9):**

```bash
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/api/3/filter" -d '{
  "name": "IP-Stale-Eligible",
  "description": "T9 stale-scan eligibility (JiraImplementation.md §9.2 Solo profile)",
  "jql": "issuetype != Sub-task AND project in (CORE-PREP, EXTENDED) AND \"Lifecycle\" = \"Active\" AND \"Last Worked At\" <= -90d AND \"Has Had Test\" = false AND (labels IS EMPTY OR labels != \"runner-system\") AND status not in (Done)"
}'
```

**Post-create verification** — the External Runner's `python -m runner poll` bootstrap self-check ([`ExternalRunner.md §3.3`](./ExternalRunner.md)) queries each filter by name and fails fast with `BootstrapIncompleteError` if any view still matches the System Config issue. To pre-empt that, run:

```bash
for F in IP-Working-Set IP-Stale IP-Paused-FIFO IP-Archive IP-Velocity-LT IP-Stale-Eligible; do
  HITS=$(curl -s "${AUTH[@]}" "$JIRA_BASE_URL/rest/api/3/search/jql?jql=filter%3D%22$F%22%20AND%20labels%3D%22runner-system%22&maxResults=0" | jq .total)
  echo "$F: $HITS system-config hits (must be 0)"
done
```

### A.6 Board + first Sprint (Working Set)

The Scrum project template in A.1 auto-creates a board. Retrieve its ID and create the first Sprint:

```bash
# Get the board ID for CORE-PREP
BOARD_ID=$(curl -s "${AUTH[@]}" \
  "$JIRA_BASE_URL/rest/agile/1.0/board?projectKeyOrId=CORE-PREP&type=scrum" \
  | jq -r '.values[0].id')

# Create Cycle 1 (per JiraImplementation.md §6.0 Sprint = Working Set)
curl -s "${AUTH[@]}" "${HDR[@]}" -X POST "$JIRA_BASE_URL/rest/agile/1.0/sprint" -d '{
  "name":          "Cycle 1 — bootstrap",
  "originBoardId": '"$BOARD_ID"',
  "goal":          "Validate Jira Stateless Runner Posture J-C end-to-end on a pilot Unit."
}'
```

Board **columns** (`To Do`, `In Progress`, `Done`) are template defaults and need no API call. Board **swimlanes by Lifecycle** ([`JiraImplementation.md §6.1`](./JiraImplementation.md)) **cannot be configured via the REST API on Free tier** — see Part B.6.

---

## Part B — Manual UI Configuration

Jira Cloud **Free** does not expose the Automation REST API. The rules in this part **must** be created through the Jira UI. The copy-paste blocks below give the exact field names and values — no free-form translation is required.

### B.1 Rule 3 — Lifecycle field-change → T5 / T6 / T7 / T8

Rule 3 is the normative writer of `LastTransitionedAt`, `LastSubtaskCompletedAt`, and (on T5/T6) a fresh Learn Subtask. It fires whenever `Lifecycle` changes, whether the change is manual or driven by the §9.1 buttons. Per [`JiraImplementation.md §4.3`](./JiraImplementation.md).

**Navigation:** Jira → **Project settings** → **Automation** → **Create rule**.

**Rule configuration (copy-paste):**

```
Name:         Runner - Rule 3 - Lifecycle transitions (T5/T6/T7/T8)
Scope:        Single project (repeat per project: CORE-PREP, EXTENDED, Independent-*)

Trigger:      Field value changed
  Fields to monitor:                Lifecycle
  Change type:                      Any changes to the field value
  For:                              Edit issue, Automation rule actions (both)
  Issue types:                      Problem, Concept, Implementation, Pattern, Debug

Condition:    Issue fields condition
  Field:                            labels
  Condition:                        does not contain
  Value:                            runner-system
  (Prevents Rule 3 firing on the System Config issue.)

Branch:       If/else — four mutually exclusive branches on the NEW Lifecycle value:

  Branch T7 — if Lifecycle = Archived
    Action: Edit issue fields
      Last Transitioned At := {{now}}
    Action: Add comment (internal)
      [Runner][T7] Archived via Lifecycle change.

  Branch T8 — if Lifecycle = Paused
    Action: Edit issue fields
      Paused At             := {{now}}
      Last Transitioned At  := {{now}}
    Action: Add comment
      [Runner][T8] Paused via Lifecycle change.

  Branch T5 / T6 — if Lifecycle = Active
    Sub-condition: previous value
      If previous Lifecycle = Archived  →  T5 (Unarchive)
      If previous Lifecycle = Paused    →  T6 (Resume)
    Action: Edit issue fields
      Last Transitioned At := {{now}}
      Last Worked At       := {{now}}
    Action: Add comment
      [Runner][T5 or T6] Resumed to Active.
```

**Important:** Rule 3 does **not** create Subtasks on T5/T6. The External Runner's Rule 1 / Rule 2 logic spawns the appropriate Learn or Revise Subtask based on the post-transition tuple — keeping Subtask-creation centralised in the GitHub-side logic per [`ExternalRunner.md §1.1`](./ExternalRunner.md).

### B.2 §9.1 Manual-Trigger — `Solo-Archive` button

**Navigation:** Project settings → **Automation** → **Create rule**.

```
Name:         Runner - Solo-Archive (manual trigger)
Scope:        Single project (repeat per project)

Trigger:      Manual trigger from issue  (this renders the "Archive" button on the issue panel)
  Issue types:                      Problem, Concept, Implementation, Pattern, Debug
  Button label:                     Archive

Condition:    Issue fields condition
  Field:                            Lifecycle
  Condition:                        is one of
  Values:                           Active, Paused

Action:       Edit issue fields
  Lifecycle                         := Archived
  (Rule 3 will fire T7 automatically on the Lifecycle field-change.)
```

### B.3 §9.1 Manual-Trigger — `Solo-Pause` button

```
Name:         Runner - Solo-Pause (manual trigger)
Trigger:      Manual trigger from issue
  Button label:                     Pause
  Issue types:                      Problem, Concept, Implementation, Pattern, Debug

Condition:    Issue fields condition
  Field:                            Lifecycle
  Condition:                        equals
  Value:                            Active

Action:       Edit issue fields
  Lifecycle                         := Paused
  (Rule 3 will fire T8 on the Lifecycle field-change, which also writes Paused At.)
```

### B.4 §9.1 Manual-Trigger — `Solo-Resume` button

```
Name:         Runner - Solo-Resume (manual trigger)
Trigger:      Manual trigger from issue
  Button label:                     Resume
  Issue types:                      Problem, Concept, Implementation, Pattern, Debug

Condition:    Issue fields condition
  Field:                            Lifecycle
  Condition:                        is one of
  Values:                           Paused, Archived

Action:       Edit issue fields
  Lifecycle                         := Active
  (Rule 3 will fire T6 if previously Paused, T5 if previously Archived.)
```

### B.5 Notification scheme tuning

Reduce noise from Jira's default "every update notifies everyone" to the signal the audit trail actually provides.

**Navigation:** Jira → your profile (top-right) → **Personal settings** → **Notifications**.

```
Issue commented                :  ON    (carries [Runner][Tn] audit trail — signal)
Issue assigned                 :  ON    (solo-user: reporter = assignee)
Issue created                  :  ON    (confirms Rule 1 fired)
Issue updated (field edits)    :  OFF   (every Runner write would notify — excessive)
Issue resolved                 :  ON
Issue closed                   :  ON
```

### B.6 Board configuration — Status × Lifecycle swimlanes

Per [`JiraImplementation.md §6.1`](./JiraImplementation.md), the Scrum board's swimlanes must be configured **by the `Lifecycle` custom field** to yield the Status × Lifecycle matrix. This setting is UI-only on Cloud Free.

**Navigation:** Scrum board → **... (board menu)** → **Configure board** → **Swimlanes** tab.

```
Base Swimlane on:   Queries
Swimlanes:
  1. Name: Active      — Query: "Lifecycle" = "Active"
  2. Name: Paused      — Query: "Lifecycle" = "Paused"
  3. Name: Archived    — Query: "Lifecycle" = "Archived"
Default swimlane:   (Everything else) — empty / none — all Lifecycle values are covered above.
```

Card layout (same board-menu → Card layout tab) should show `Work Type`, `Revision Done / Revision Target` on Parent cards, and `Labels`, `Outcome`, `Due Date` on Sub-task cards. Repeat per project.

---

## Part C — GitHub Secrets (wiring the External Runner)

Per [`ExternalRunner.md §8.2`](./ExternalRunner.md), all Jira credentials **must** live exclusively in GitHub Secrets at repo scope. No manual editing of workflow YAMLs is acceptable; every `${{ secrets.* }}` reference already exists in the workflow files.

**Navigation:** `jira-stateless-runner` repo → **Settings → Secrets and variables → Actions → New repository secret**.

| Secret name | Value | Source |
|---|---|---|
| `JIRA_BASE_URL` | `https://<yoursite>.atlassian.net` | §0.2 |
| `JIRA_EMAIL` | Your Atlassian account email | §0.2 |
| `JIRA_TOKEN` | The token from §0.2 (treated as password) | §0.2 |

`GITHUB_TOKEN` is auto-provided by GitHub Actions — do not create it manually.

**CLI alternative** (once `gh` is authenticated to your repo):

```bash
gh secret set JIRA_BASE_URL --body "https://<yoursite>.atlassian.net"
gh secret set JIRA_EMAIL    --body "<you>@example.com"
gh secret set JIRA_TOKEN    # prompts for value interactively
```

---

## Part D — Smoke Test (first `workflow_dispatch` run)

A single manual run validates the full chain: Jira credentials → watermark read → changelog fetch → idempotency label check → (no events yet, so no dispatch) → watermark write → audit-comment capability.

```bash
# Trigger the runner and stream its logs
gh workflow run poll-dispatch.yml --repo <owner>/jira-stateless-runner
gh run watch --repo <owner>/jira-stateless-runner
```

**Expected behaviour on a fresh site:**

1. Runner starts; reads System Config issue; sees empty `Last Processed Changelog Id` → treats as `0`.
2. Bootstrap self-check (per [`ExternalRunner.md §3.3`](./ExternalRunner.md)) queries each filter for `labels = "runner-system"` hits → all return 0 → pass.
3. No user changelog events yet → no dispatches.
4. Writes `Last Successful Poll At := now`, `Runner Version := 0.1.1` to System Config issue.
5. Job exits green in ~30 s.

**Then create one pilot Unit** in Jira (Problem, Stage=Intermediate, Difficulty=Medium) and re-trigger:

```bash
gh workflow run poll-dispatch.yml --repo <owner>/jira-stateless-runner
gh run watch --repo <owner>/jira-stateless-runner
```

**Expected this time:**

1. Runner picks up the `jira:issue_created` changelog entry.
2. Rule 1 / T1 fires → creates `[Intermediate][Learn] — <summary>` Sub-task; seeds `Revision Target := 3` (from Difficulty=Medium); posts `[Runner][T1]` audit comment on the Unit.
3. Idempotency label `idem:<hex>` appears on the new Sub-task.
4. Watermark advances to the changelog ID just processed.

Verify in Jira: open the pilot Unit → Comments tab shows the `[Runner][T1]` line; the Unit has one Sub-task. Now drag the Sub-task to Done and repeat the dispatch — T2 should fire within 5 minutes on the cron, or immediately on a manual `workflow_dispatch`.

---

## Part E — Verification Checklist

Run through this list once, top to bottom, before enabling the `*/5` cron in production:

| # | Check | How | Expected |
|---|---|---|---|
| E1 | API token valid | `curl … /myself` | returns accountId |
| E2 | Projects created | `curl … /project` | `CORE-PREP`, `EXTENDED` present |
| E3 | 16 custom fields exist | `curl … /field` | all 16 names in response |
| E4 | System Config issue in each project | `search jql="labels=runner-system"` | 1 hit per project |
| E5 | All 7 filters created | `curl … /filter/search?filterName=IP-` | 7 hits |
| E6 | Filters exclude System Config | per-filter `AND labels = "runner-system"` query | 0 hits per filter (E.1–E.6) |
| E7 | Rule 3 active in each project | Jira UI → Project settings → Automation | `Runner - Rule 3` shown as enabled |
| E8 | 3 Manual-Trigger buttons visible on a Unit | Open any Unit issue in Jira | `Archive`, `Pause`, `Resume` buttons render in issue panel |
| E9 | Board swimlanes are `Active / Paused / Archived` | Open the Scrum board | 3 swimlanes match Lifecycle values |
| E10 | Sprint `Cycle 1 — bootstrap` exists | Backlog view | Sprint present; can be started |
| E11 | 3 GitHub Secrets set | `gh secret list --repo <owner>/jira-stateless-runner` | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_TOKEN` present |
| E12 | Smoke-test runs green | `gh run list --limit 1` | latest poll-dispatch run = success |
| E13 | T1 fires on a pilot Unit | Create Unit → dispatch → inspect | Sub-task + audit comment present |
| E14 | Pause button fires T8 | Click `Pause` on a Unit → inspect | `Lifecycle = Paused`; `Paused At` set; audit comment present |
| E15 | Stale scan workflow exists | `gh workflow list --repo <owner>/jira-stateless-runner` | `runner-stale-scan` and `runner-healthcheck` present |

Once every row is ✅, the runner is cleared to run on its `*/5 * * * *` cron.

---

## Part F — Daily Operational Workflow

Once setup is green (Part E), the daily workflow divides strictly along the Stateless-Runner boundary: **the user interacts only with Jira**; **GitHub Actions runs in the background** and never asks for input. The runner's only channels back to the user are Jira audit comments (Layer 2) and — on fatal outages — a GitHub alert issue ([`ExternalRunner.md §6.4`](./ExternalRunner.md)).

### F.1 Per-study-session user actions (multiple times per day)

| # | Action | Where | Triggers | Fields touched |
|---|---|---|---|---|
| U1 | **Create a Unit** | Jira → Create → `Problem` / `Concept` / `Implementation` / `Pattern` / `Debug` | **T1** on next poll | `Summary`, `Stage` (required); `Difficulty` optional — fallback seeds `Revision Target := 2` per [`ExternalRunner.md §4.1`](./ExternalRunner.md) |
| U2 | **Mark a Learn Subtask Done** | Open Sub-task → Done | **T2** on next poll | `Status := Done` |
| U3 | **Mark a Revise Subtask Done — Pass** | Set `Outcome := Pass` → Done | **T3** if `RevisionDone + 1 < RevisionTarget`; **T4** if equal (auto-Pause) | `Outcome`, `Status` |
| U4 | **Mark a Revise Subtask Done — Regress** | Set `Outcome := Regress` → Done | **T12** — resets `RevisionDone := 0`, spawns fresh Learn | `Outcome`, `Status` |
| U5 | **Mark a Test Subtask Done** | Set `Outcome` → Done | **T13** on Regress (LSC + re-enter Learn); Pass writes `LastSubtaskCompletedAt` only | `Outcome`, `Status` |
| U6 | **Manual runner trigger** (optional) | `gh workflow run poll-dispatch.yml` or Actions UI | Bypasses 5-min cron | — |

**What the user never does manually:** create Subtasks, compute due dates or `RevisionDone` / `RevisionTarget`, write `Last Worked At` / `Last Transitioned At` / `Paused At`, or scan for staleness — all owned by Rules 1 / 2 / 4 per [`ExternalRunner.md §4`](./ExternalRunner.md).

### F.2 Per-Working-Set user actions (every ~10–15 days)

Per [`LivingRequirements.md §6 FR6`](./LivingRequirements.md) v0.7.8, the Sprint **is** the Working Set — no additional cycle abstraction.

| # | Action | Where | Filter consulted |
|---|---|---|---|
| U7 | **Plan a Working Set** | Scrum board → Backlog → Create Sprint → drag Units in → Start | `IP-Paused-FIFO` + `IP-Working-Set` |
| U8 | **Close the Working Set** | Scrum board → Complete Sprint | `IP-Velocity-LT` |

### F.3 §9.1 Manual-Trigger buttons — ad hoc (rare)

Rendered on every Unit's issue panel; each click updates `Lifecycle` and delegates side-effects to **Rule 3** (Jira Automation, per Part B.1).

| # | Button | Field write | Rule 3 fires | Transition |
|---|---|---|---|---|
| U9 | **Pause** | `Lifecycle := Paused` | ✓ | **T8** — writes `Paused At`, `Last Transitioned At` |
| U10 | **Resume** (from Paused) | `Lifecycle := Active` | ✓ | **T6** — writes `Last Transitioned At`, `Last Worked At` |
| U11 | **Resume** (from Archived) | `Lifecycle := Active` | ✓ | **T5** — writes `Last Transitioned At`, `Last Worked At` |
| U12 | **Archive** | `Lifecycle := Archived` | ✓ | **T7** — writes `Last Transitioned At` |

After T5/T6, Rule 2 on the next poll may spawn a fresh Learn Subtask if no actionable Sub-task exists — centralising Subtask creation on the GitHub side per [`ExternalRunner.md §1.1`](./ExternalRunner.md).

### F.4 Reading the board — which filter for which question

| Question | Filter (v0.7.9) |
|---|---|
| "What should I work on in the next 3 days?" | `IP-Now` |
| "What's currently in my Working Set?" | `IP-Working-Set` |
| "What have I left idle for 90 days?" | `IP-Stale` |
| "What's paused — anything to resume?" | `IP-Paused-FIFO` |
| "What's archived?" (reference) | `IP-Archive` |
| "How fast am I moving?" | `IP-Velocity-LT` |
| (runner-internal, used by Rule 4) | `IP-Stale-Eligible` |

All six user-facing non-Sub-task filters include the mandatory `AND (labels IS EMPTY OR labels != "runner-system")` exclusion per [`JiraImplementation.md §5`](./JiraImplementation.md) v0.7.9.

### F.5 `poll-dispatch.yml` — Rules 1 & 2 (every 5 minutes)

**Cron:** `*/5 * * * *` (also `workflow_dispatch` for manual trigger)

```
1. Read System Config issue  →  Last Processed Changelog Id (watermark).
2. Fetch Jira changelog since watermark.
3. Bootstrap self-check (first run only): filters MUST exclude runner-system
   → else BootstrapIncompleteError (ExternalRunner.md §3.3).
4. Per event:
      ┌─ issue_created on Unit type  →  Rule 1 (T1)
      │     • Guard: Stage present; Difficulty optional (fallback Target=2)
      │     • Idempotency check: idem:<hex> label on any existing Sub-task
      │     • Create [Stage][Learn] Sub-task, 2 SP
      │     • Seed Revision Target (from Difficulty or default 2)
      │     • Post [Runner][T1] audit comment
      │
      └─ subtask status → Done  →  Rule 2 dispatch
            • state_machine.dispatch(tuple, Outcome) → Tn
            • T2:  Learn Done            → spawn Revise#1   (due +2 bd)
            • T3:  Revise Pass, more     → spawn Revise#k+1 (due +gap[k])
            • T4:  Revise Pass, target   → Lifecycle := Paused (auto-park)
            • T12: Revise Regress        → RevisionDone := 0; spawn Learn
            • T13: Test Regress          → LSC + re-enter Learn
            • Post [Runner][Tn] audit comment; write idem:<hex> label
5. Write Last Successful Poll At := now to System Config.
6. On fatal error: increment consecutive_failures; if threshold crossed,
   `gh issue create` (ExternalRunner.md §6.4 dead-man's-switch).
```

**Idempotency:** `sha256(unit_key|event_id|transition_id)` stored as `idem:<hex>` Jira label. Replay of the same event is a no-op.

### F.6 `stale-scan.yml` — Rule 4 / T9 (Monday 10:00)

**Cron:** `0 10 * * MON`

```
1. Query IP-Stale-Eligible:
      issuetype != Sub-task
      AND project in (CORE-PREP, EXTENDED)   ← Independent projects excluded
      AND "Lifecycle" = "Active"
      AND "Last Worked At" <= -90d
      AND "Has Had Test" = false             ← durable lifetime guard
      AND (labels IS EMPTY OR labels != "runner-system")
      AND status not in (Done)
2. Per match:
      • Create [Stage][Test] Sub-task, due +2 bd (T9)
      • Write Has Had Test := true           ← fires at most ONCE per Unit
      • Post [Runner][T9] audit comment; write idem:<hex>
3. Write Last Stale Scan At := now to System Config.
```

**Design invariant:** `Has Had Test = true` is the **lifetime-idempotency flag** ([`JiraImplementation.md §9.2`](./JiraImplementation.md)). T9 fires at most once per Unit per human lifetime, by policy. Independent projects (`GOOGLE150`, `META150`, …) do not participate in Rule 4 by construction.

### F.7 `healthcheck.yml` — dead-man's-switch (every 6 hours)

**Cron:** `0 */6 * * *`

```
1. Read Last Successful Poll At from System Config.
2. If now − last_success > 30 min  →  */5 cron has stalled.
3. If no alert is currently open:
      gh issue create
        --title  "Runner System Alert: cron-stall"
        --label  "system-alert,runner"
        --body   <remediation template>
4. Mirror alert URL into System Config's Open Alert Issue Url field
   (survives GH Cache eviction).
```

Same pattern fires on `http_401` (credential rotation), `http_429` (rate-limit), and repeated `http_5xx` (Jira outage) per [`ExternalRunner.md §6.4`](./ExternalRunner.md).

### F.8 Single-Unit lifecycle walkthrough (108 days)

Unit "Longest Increasing Subsequence" — Stage=Intermediate, Difficulty=Medium. Left column = Jira state (authoritative); right column = GitHub runner activity (pure logic).

| Time | User in Jira | Runner in GitHub Actions |
|---|---|---|
| T+0m | U1: Creates `Problem` "LIS"; Stage=Intermediate, Difficulty=Medium | (idle — next cron in ≤5 min) |
| T+2m | — | `poll-dispatch`: **Rule 1 / T1**. Creates `[Intermediate][Learn] — LIS`; seeds `Revision Target := 3`; writes idem label; posts `[Runner][T1]`; advances watermark |
| T+30m | U2: Learn Sub-task → Done | — |
| T+34m | — | `poll-dispatch`: **Rule 2 → T2**. Spawns `Revise#1` (due +2 bd); `Work Type := Revise`; timestamps updated; `[Runner][T2]` |
| T+2d | U3: Revise#1 → `Outcome=Pass` → Done | — |
| T+~5m later | — | **T3**: `RevisionDone 0→1`; spawns Revise#2 (due +5 bd) |
| T+7d | U3: Revise#2 → Pass → Done | — |
| T+~5m later | — | **T3**: `RevisionDone 1→2`; spawns Revise#3 (due +11 bd) |
| T+18d | U3: Revise#3 → Pass → Done | — |
| T+~5m later | — | **T4** (target hit: `RevisionDone + 1 == RevisionTarget = 3`). No more Revise spawned. Writes `Lifecycle := Paused`, `Paused At := now`; `[Runner][T4] Revise#3 → Paused` |
| T+18d→T+108d | (Unit sits in `IP-Paused-FIFO`; user ignores it) | (stale-scan weekly: Paused Units NOT in `IP-Stale-Eligible` because filter requires `Lifecycle = Active`) |
| T+20d | U10: (hypothetical) Clicks **Resume** | **Rule 3** (Jira Automation) → **T6**; then next `poll-dispatch` → Rule 2 may spawn Revise#4 if no actionable Sub-task |
| T+108d | (assume left Active, never worked — 90 days idle) | `stale-scan` Monday 10:00: matches `IP-Stale-Eligible`. **Rule 4 / T9**: creates `[Intermediate][Test] — LIS` (due +2 bd); **`Has Had Test := true`** (durable); `[Runner][T9]` |
| T+110d | U5: Test → `Outcome=Pass` → Done | `poll-dispatch`: Test-pass branch — LSC update only; no tuple motion. `Has Had Test` stays `true` forever |

### F.9 Where state lived at each moment

| State | Authoritative location | Derived location | Runner "remembers" between invocations? |
|---|---|---|---|
| `RevisionDone`, `RevisionTarget`, `Work Type`, `Lifecycle`, `Stage`, `Outcome` | Jira Unit / Sub-task custom fields | — | **No** — read fresh every poll |
| Watermark `Last Processed Changelog Id` | Jira System Config issue | (mirrored to GH Cache) | **No** — read fresh from Jira |
| Idempotency keys | Jira `idem:<hex>` labels on Sub-tasks | — | **No** — checked per event |
| Lifetime T9 guard | Jira `Has Had Test` field | — | **No** |
| Dispatch table (D1–D23) | Python source (pure data) | — | N/A — deterministic |
| Sprint membership | Jira Sprint field | — | N/A — runner does not read |

**What this proves:** if at any point in the 108-day walkthrough the `jira-stateless-runner` repo had been deleted and re-cloned, the next cron tick would resume at the exact same watermark, recompute the same idempotency labels (finding existing ones → no-ops on already-processed events), and continue onward. **The only irreplaceable store is Jira.**

**Transitions exercised:** T1 (create), T2 (Learn→Revise), T3 ×2 (Revise Pass), T4 (target hit), T6 (Resume), T9 (stale scan), Test-pass LSC. Not demonstrated in this walkthrough: T5 (Unarchive), T7 (Archive button), T8 (Pause button), T12 (Revise Regress), T13 (Test Regress) — all follow the same pattern: user action in Jira → runner dispatch on next poll → pure `state_machine.dispatch()` → side-effects written back with idem label + audit comment.

### F.10 Division of labour — summary

| Layer | What it does | What it does NOT do |
|---|---|---|
| **User (Jira UI)** | Create Units, transition Subtasks to Done, set `Outcome`, click §9.1 buttons, plan Sprints | Create Subtasks, compute due dates, tick `RevisionDone`, scan for staleness |
| **Jira (State Substrate)** | Hold all custom fields, System Config watermark, idempotency labels, `Has Had Test`, audit-comment history, Sprint boundaries | Execute T1–T13 logic (Rule 3 is the sole Jira-side exception — T5/T6/T7/T8 timestamp writes) |
| **Jira Automation (Rule 3 + §9.1 buttons)** | On `Lifecycle` field-change: write `Paused At`, `Last Transitioned At`, `Last Worked At`; post short audit comment. **~15 runs/month → fits Free-tier 100-run cap** | Create Subtasks, run JQL scans, compute RevisionDone math |
| **GitHub Actions (Logic Engine)** | Rule 1 / Rule 2 (`poll-dispatch`), Rule 4 (`stale-scan`), dead-man's-switch (`healthcheck`) | Hold any authoritative state; ask user for input |

Day in, day out: **create Units, tick Subtasks Done with an Outcome, occasionally click a button.** Everything else is the runner's job.

---

## Stateless-Runner principle — recap

This guide's provisioning steps collectively satisfy the principle declared in [`ExternalRunner.md §1.1`](./ExternalRunner.md): **Jira holds all state, GitHub holds only logic.**

| What lives where | Artefact | Substrate |
|---|---|---|
| Polling watermark | `Last Processed Changelog Id` on System Config issue | Jira (authoritative) |
| Runner version | `Runner Version` on System Config issue | Jira (authoritative) |
| Idempotency keys | `idem:<hex>` labels on Sub-tasks | Jira (authoritative) |
| T9 lifetime guard | `Has Had Test` Boolean on Unit | Jira (authoritative) |
| Unit state tuple | `Stage`, `WorkType`, `Lifecycle`, `RevisionDone`, `RevisionTarget` | Jira (authoritative) |
| Audit trail | `[Runner][Tn]` comments on Unit | Jira (authoritative) |
| Technical run logs | GH Actions run JSON | GitHub (90-day retention; derived) |
| Dispatch health counter | `consecutive_failures`, `recovery_streak` | GH Cache (ephemeral; mirrored to Jira for durability) |
| Logic binary | Python 3.11 source | GitHub (pure logic) |

The corollary: re-cloning the runner repo on a new machine and running `gh workflow run poll-dispatch.yml` resumes exactly where the previous runner left off — the only required state is the three GitHub Secrets plus whatever Jira already has.

---

## Changelog

- **v0.1.2 (2026-04-19)** — Migrates §A.3 screen attachment and §A.2 field defaults from Part B (manual UI) to Part A (API-automated) via the new `ensure_field_screen_attachments` and `ensure_field_defaults` methods in `scripts/provision_jira.py`. Uses the Issue Type Screen Scheme → Screen Scheme → Screen chain (`/rest/api/3/issuetypescreenscheme/…`) for screen resolution and `PUT /rest/api/3/field/{fid}/context/defaultValue` for the three select-list defaults plus the best-effort `Revision Done=0` numeric default. Scope-split table updated so Part A now lists "screen attachments, defaults". No normative changes.
- **v0.1.1 (2026-04-18)** — Adds **Part F — Daily Operational Workflow** covering post-setup steady-state: F.1 per-session user actions (U1–U6) with transition-ID mapping; F.2 per-Working-Set actions (U7–U8) aligned to Sprint = Working Set per [`LivingRequirements.md §6 FR6`](./LivingRequirements.md); F.3 §9.1 button → Rule 3 → transition mapping (U9–U12 → T5/T6/T7/T8); F.4 filter-to-question map for the six v0.7.9 user-facing filters; F.5 `poll-dispatch.yml` execution flow for Rules 1 & 2; F.6 `stale-scan.yml` execution flow for Rule 4 / T9 with the `Has Had Test` lifetime-idempotency invariant; F.7 `healthcheck.yml` dead-man's-switch flow; F.8 108-day single-Unit lifecycle walkthrough exercising T1, T2, T3×2, T4, T6, T9, and Test-pass LSC; F.9 state-location proof table demonstrating Jira as sole authoritative substrate; F.10 division-of-labour summary across User / Jira / Jira Automation / GitHub Actions. Scope-split table in the header updated. No normative changes; no edits to [`LivingRequirements.md`](./LivingRequirements.md), [`JiraImplementation.md`](./JiraImplementation.md), or [`ExternalRunner.md`](./ExternalRunner.md).
- **v0.1.0 (2026-04-18)** — Initial provisioning guide, derived from [`LivingRequirements.md`](./LivingRequirements.md) v0.7.8, [`JiraImplementation.md`](./JiraImplementation.md) v0.7.9, and [`ExternalRunner.md`](./ExternalRunner.md) v0.1.1. Categorises provisioning into API-automated (Part A: projects, 16 custom fields, 7 v0.7.9 saved filters with mandatory `labels != "runner-system"` exclusion, System Config issue, board + Sprint) vs. manual UI (Part B: Rule 3, §9.1 Manual-Trigger buttons `Archive` / `Pause` / `Resume`, notification scheme, board swimlanes) because Jira Cloud Free does not expose the Automation REST API. Adds Part C (GitHub Secrets), Part D (smoke test), and Part E (15-row verification checklist). Non-normative; adds no requirements.
