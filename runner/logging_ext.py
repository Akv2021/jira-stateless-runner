"""Privacy-safe structured JSON logger (docs/ExternalRunner.md §5.1, §8.3).

Enforces an allow-list of fields permitted at INFO level. ``summary``,
``description`` and ``comment`` content are filtered out of INFO
payloads to protect user-generated content in the public runner's
GitHub Actions console; DEBUG-level logs carry the full payload for
local troubleshooting only. The allow-list is the authoritative policy
-- any attempt to log a disallowed field via
``logger.info("summary: %s", unit.summary)`` silently drops the field
on its way out. Regression coverage lives in ``tests/test_logging.py``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Final

_ALLOWED_INFO_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "ts",
        "lvl",
        "run_id",
        "event_id",
        "unit",
        "subtask",
        "transition",
        "idem_key",
        "stage",
        "work_type",
        "lifecycle",
        "rev_done",
        "rev_target",
        "outcome",
        "due",
        "msg",
    }
)
"""Fields permitted on INFO-level payloads (§8.3).

Adding a field here is a deliberate policy act -- any unknown extra on
a log record is silently dropped at INFO, retained at DEBUG/WARN/ERROR.
"""

_RESERVED_LOG_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)
"""Built-in ``logging.LogRecord`` attributes to exclude from payload extraction."""


class StructuredFormatter(logging.Formatter):
    """JSON formatter with INFO-level allow-list enforcement (§8.3).

    DEBUG / WARN / ERROR records carry their full ``extra`` payload so
    local debugging is unaffected; INFO records are reduced to the
    ``_ALLOWED_INFO_FIELDS`` intersection before serialisation.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = self._collect(record)
        # INFO is the production level; DEBUG retains full payload for local
        # debugging per §8.3 table ("DEBUG only, local runs only").
        if record.levelno == logging.INFO:
            payload = {k: v for k, v in payload.items() if k in _ALLOWED_INFO_FIELDS}
        return json.dumps(payload, default=str, sort_keys=True)

    def _collect(self, record: logging.LogRecord) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "lvl": record.levelname,
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        return payload


def get_logger(name: str) -> logging.Logger:
    """Return a logger wired to ``StructuredFormatter`` on stdout.

    Safe to call repeatedly: the handler is attached only once per
    logger so repeated invocations in tests / CLI entrypoints do not
    produce duplicate lines.
    """
    logger = logging.getLogger(name)
    already = any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
    if not already:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


__all__ = ["StructuredFormatter", "get_logger"]
