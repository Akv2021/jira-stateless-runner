"""CLI entry point: ``python -m runner {poll,stale,health}``.

Subcommands map to the three scheduled workflows in docs/ExternalRunner.md §4:

- ``poll``   -> Rules 1 + 2 (every 5 minutes via poll-dispatch.yml)
- ``stale``  -> Rule 4 / T9 (Monday 10:00 via stale-scan.yml)
- ``health`` -> dead-man's-switch (every 6 hours via healthcheck.yml)
"""

from __future__ import annotations

import sys

from runner.cli import main

if __name__ == "__main__":
    sys.exit(main())
