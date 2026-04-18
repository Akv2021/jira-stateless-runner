"""Privacy-safe structured JSON logger.

Per docs/ExternalRunner.md §8.3: enforces an allow-list of fields permitted at
INFO level. ``summary``, ``description``, and ``comment`` content are filtered
out of INFO payloads to protect user-generated content in the public runner's
GitHub Actions console. DEBUG-level logs carry the full payload for local
troubleshooting only.

Implementation lands in M9; regression tests in tests/test_logging.py.
"""
