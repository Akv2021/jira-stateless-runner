"""Dead-man's-switch state machine.

Per docs/ExternalRunner.md §6.3-§6.4:

- ``with_health_tracking`` decorator increments ``consecutive_failures`` on raise.
- Crossing threshold → ``open_alert()`` via ``gh issue create``.
- 3 consecutive successful runs → ``maybe_close_alert()`` auto-closes the alert.

Mirrors alert-issue URL to the System Config ``Open Alert Issue Url`` field.

Implementation lands in M9.
"""
