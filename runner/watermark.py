"""Polling watermark I/O against the Jira System Config issue.

Per docs/ExternalRunner.md §3:

- ``read()``    → ``Last Processed Changelog Id`` (None treated as 0).
- ``write(id)`` → writes watermark + ``Last Successful Poll At`` + ``Runner Version``.
- ``BootstrapIncompleteError`` self-check per §3.3 — blocks the runner when any
  user-facing JQL filter is missing ``AND labels != "ztmos-system"``.

Implementation lands in M8 (requires Phase 1 Jira provisioning complete).
"""
