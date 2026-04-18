"""CLI entry point: ``python -m runner {poll,stale,health}``.

Subcommands map to the three scheduled workflows in docs/ExternalRunner.md §4:

- ``poll``   → Rules 1 + 2 (every 5 minutes via poll-dispatch.yml)
- ``stale``  → Rule 4 / T9 (Monday 10:00 via stale-scan.yml)
- ``health`` → dead-man's-switch (every 6 hours via healthcheck.yml)

Implementation lands in later milestones (M5-M9).
"""

from __future__ import annotations


def main() -> None:
    """Dispatch to the requested subcommand. Implemented in M5+."""
    raise NotImplementedError("CLI implementation lands in M5+")


if __name__ == "__main__":
    main()
