"""jira-stateless-runner: stateless Jira state-machine runner.

Polls Jira changelog events and dispatches T1–T13 transitions per the
declarative rules table in docs/ImplementationTestMatrix.md §3 and the
specification in docs/LivingRequirements.md §5.2.
"""

__version__ = "0.1.0"
