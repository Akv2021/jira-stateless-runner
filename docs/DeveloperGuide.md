# jira-stateless-runner — Developer Cheat Sheet

Centralises every CLI command you'll hit during day-to-day work on this repo. Everything here is wired through `pyproject.toml`, `.pre-commit-config.yaml`, and `.gitleaks.toml` — if behaviour drifts, fix the config, not this doc.

---

## At a glance

| # | Command | Category | Frequency |
|---|---|---|---|
| 1 | `pip install -e '.[dev]'` | Environment | Once per clone; after `pyproject.toml` changes |
| 2 | `ruff check .` | Quality gate | Per save / before push |
| 3 | `ruff format .` | Quality gate | Per save / before push |
| 4 | `mypy runner` | Quality gate | Before push / PR |
| 5 | `pytest` | Testing | **Every code change; M1/M2 gate** |
| 6 | `pre-commit install` | Hooks | **Once per clone** |
| 7 | `pre-commit run --all-files` | Hooks | First smoke test; after `autoupdate`; before big PR |
| 8 | `pre-commit autoupdate` | Hooks | Quarterly |
| 9 | `gitleaks detect --redact --no-git` | Security | Ad-hoc; CI runs it automatically |
| 10 | `python -m runner {poll,stale,health}` | Runtime | Per cron; `workflow_dispatch` for smoke runs |
| 11 | `python scripts/provision_jira.py` | Bootstrap | Once per Jira tenant; idempotent |

---

## 1. Environment Setup

### `pip install -e '.[dev]'`

- **Does:** Installs the `jira-stateless-runner` package in editable mode (`-e`) so local `runner/` edits take effect immediately without reinstall, and pulls in every `dev` extra (`pytest`, `pytest-httpx`, `ruff`, `mypy`, `pre-commit`, `types-python-dateutil`).
- **When:** After cloning, creating a fresh venv, or changing `[project.dependencies]` / `[project.optional-dependencies]` in `pyproject.toml`.
- **Prereq:** Active Python 3.11 virtualenv (`python3.11 -m venv .venv && source .venv/bin/activate`).
- **Green signal:** Exit 0; `pip list` shows `jira-stateless-runner 0.1.0` at its local editable location.

---

## 2. Quality Gates

### `ruff check .`
- **Does:** Runs the linter using `[tool.ruff.lint]` in `pyproject.toml` — enforces pycodestyle (E/W), pyflakes (F), isort (I), bugbear (B), pyupgrade (UP), simplify (SIM), naming (N), and ruff-specific (RUF) rules.
- **When:** Before every push; auto-fires in pre-commit. Add `--fix` to auto-apply safe fixes.
- **Green signal:** `All checks passed!`

### `ruff format .`
- **Does:** Applies ruff's black-compatible formatter at line-length 100 (per `[tool.ruff]` in `pyproject.toml`). Idempotent.
- **When:** Before every push; auto-fires in pre-commit. Safe to run repeatedly.
- **Green signal:** `N file(s) left unchanged` or `N file(s) reformatted`.

### `mypy runner`
- **Does:** Performs strict static type-checking on the `runner/` package under `[tool.mypy] strict = true` — rejects implicit `Any`, missing return annotations, and untyped function defs. Use `mypy runner tests` to also check tests (untyped defs allowed there via `[[tool.mypy.overrides]]`).
- **When:** Before every push; auto-fires in pre-commit. Required for every milestone Definition-of-Done (refer to `docs/ExternalRunner.md §9.4`).
- **Green signal:** `Success: no issues found in N source files`.

---

## 3. Testing

### `pytest`
- **Does:** Executes the test suite under `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, `-ra --strict-markers --strict-config`. Scoping flags: `pytest -v` (verbose), `pytest -k D7` (single matrix row), `pytest tests/test_state_machine.py` (dispatch tests only), `pytest -m integration` (M3+ mocked-Jira tests).
- **When:** Every code change. Full suite expected GREEN on every PR; the state-machine matrix in `docs/ImplementationTestMatrix.md` (23 rows D1–D23) is the hard contract.
- **Green signal:** `154 passed in 0.Xs` on `main` (post-M12).

---

## 4. Pre-commit Management

### `pre-commit install`
- **Does:** Wires the automated hooks declared in `.pre-commit-config.yaml` (trailing-whitespace, end-of-file-fixer, check-yaml, gitleaks, ruff, ruff-format, mypy) into `.git/hooks/pre-commit` so they fire on every `git commit`.
- **When:** Exactly once per cloned working tree (refer to `docs/ImplementationRoadmap.md` M0.10). Idempotent — re-running is harmless.
- **Green signal:** `pre-commit installed at .git/hooks/pre-commit`.

### `pre-commit run --all-files`
- **Does:** Bypasses git's changed-files filter and runs every hook against every file in the working tree.
- **When:** First time after `pre-commit install` (verifies the scaffold is clean); after `pre-commit autoupdate` (catches incompatibilities with new rev pins); before opening a large PR; after pulling `main`.
- **Green signal:** Every hook reports `Passed`.

### `pre-commit autoupdate`
- **Does:** Bumps the `rev:` pin under each `- repo:` in `.pre-commit-config.yaml` to the latest tagged release of that hook repo (gitleaks, ruff-pre-commit, mirrors-mypy, pre-commit-hooks). Rewrites the YAML in place — **commit the diff** afterwards. No effect on hook configuration or arguments, only on the version fetched.
- **When:** Quarterly as routine maintenance; immediately after a CVE advisory on any hook's upstream.
- **Follow-up:** Always run `pre-commit run --all-files` after an autoupdate to surface breaking changes before the next commit lands.

---

## 5. Security

### `gitleaks detect --redact --no-git`
- **Does:** Scans the working tree (not git history — `--no-git` suppresses that) using the rules in `.gitleaks.toml`: default ruleset + Atlassian-specific rules + the `docs/` and `${{ secrets.X }}` allowlists. `--redact` masks any matched secret in the console output so the transcript itself cannot leak it.
- **When:** Ad-hoc after handling any secret-adjacent material (rotating `JIRA_TOKEN`, editing workflow YAML with `secrets.*` refs, reviewing an external diff). CI runs it automatically via `.github/workflows/ci.yml` and the pre-commit hook runs it per commit — this manual invocation is a belt-and-braces check before a sensitive PR. Per the credential-handling mandate (refer to `docs/ExternalRunner.md §8.2`), secrets live in GitHub Secrets only.
- **Green signal:** `no leaks found`.
- **Red signal:** Any finding fails exit. Review, remediate, never bypass.

---

## 6. Runtime CLI (`python -m runner {poll,stale,health}`)

Every entrypoint is wrapped by `runner.cli._with_health_tracking`:
success resets the consecutive-failure counter and may auto-close an
open alert; failure increments the counter, classifies the exception
via `runner.health.classify`, and opens a GitHub System Alert issue
once the kind-specific threshold trips.

| Command | Schedule | Purpose |
|---|---|---|
| `python -m runner poll` | `*/5 * * * *` (`poll-dispatch.yml`) | Read changelog since last watermark; dispatch T1 (Rule 1) and T2–T13 (Rule 2). |
| `python -m runner stale` | `0 10 * * MON` (`stale-scan.yml`) | Weekly Rule 4 / T9 stale scan against `IP-Stale-Eligible`. |
| `python -m runner health` | `0 */6 * * *` (`healthcheck.yml`) | Dead-man's-switch against `Last Successful Poll At`. |

### Required environment variables

| Variable | Purpose |
|---|---|
| `JIRA_URL` | `https://<tenant>.atlassian.net` |
| `JIRA_USER` | Atlassian account email for Basic auth |
| `JIRA_TOKEN` | API token (never a password) |
| `JIRA_PROJECT_KEY` | Project whose issues are polled (e.g. `COREPREP`) |
| `JIRA_ACCOUNT_ID` | Account ID used as assignee fallback |

`GH_TOKEN` (for `gh issue create`) is injected by GitHub Actions at
runtime; only required locally when reproducing the alert path.

### First-run bootstrap (M8)

The runner fails fast with `BootstrapIncompleteError` unless every
non-`IP-Now` saved filter excludes `labels = "runner-system"`. Provision
the state substrate by running `python scripts/provision_jira.py` —
see `docs/JiraProvisioningGuide.md` for the step-by-step operator
checklist. On the first poll, `JiraClient.get_field_map` lazy-loads
`GET /rest/api/3/field`; every subsequent write / read is translated
bidirectionally between display names and `customfield_XXXXX` IDs.

### Typical failure modes

| Symptom | Likely cause | Resolution |
|---|---|---|
| `BootstrapIncompleteError: ... count=0` | `JIRA_PROJECT_KEY` does not match a provisioned project | Export the correct key (`COREPREP` / `EXTENDED`) and re-run. |
| `IssueNotFoundError` WARN logs | Issue deleted mid-flight (§6.1) | Informational only; classified as `not_found` and does not trip the alert. |
| `alert_open_failed: CalledProcessError` from `gh` | `gh` CLI not authenticated or `system-alert` label missing | Run `gh auth login` and create the `system-alert` + `runner` labels in the repo. |
| `changelog pagination exceeded page_cap` | Pathological issue with >2 000 history entries | Raise `page_cap` on the `iter_changelog_pages` call or investigate the issue. |

---

## Appendix — Common command combinations

```bash
# Full local CI simulation before opening a PR
ruff format . && ruff check . && mypy runner tests && pytest

# Clean-sweep everything the pre-commit gate would do
pre-commit run --all-files

# Reset local dev environment after pulling main
pip install -e '.[dev]' && pre-commit install

# Scan for credentials in the current tree only (skip git history)
gitleaks detect --redact --no-git -v
```
