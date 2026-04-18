"""Central configuration constants and runtime settings.

Per docs/ExternalRunner.md §2.2 and docs/LivingRequirements.md §6 FR4: the
domain constants below are the ONLY place where the numeric policy lives.
Any change here must be accompanied by a matching update to the normative
specifications.

Runtime settings (``Settings``) are loaded from the process environment
via pydantic-settings; use ``get_settings()`` to obtain the cached
singleton.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RevisionGap: Final[list[int]] = [2, 5, 11, 25]
"""Revise#k due-date offsets in business days.

Revise#1 is due +2bd after the preceding Sub-task closure, #2 at +5bd, #3 at
+11bd, #4 at +25bd. Per docs/LivingRequirements.md §5 cadence policy.
"""

RevisionTarget: Final[dict[str, int]] = {"Easy": 2, "Medium": 3, "Hard": 4}
"""Unit Difficulty → number of successful Revise iterations before T4 auto-Pause."""

RevisionTargetDefault: Final[int] = 2
"""Fallback RevisionTarget when Difficulty is missing at Unit creation.

Applied by Rule 1 (docs/ExternalRunner.md §4.1) when the Difficulty field is
null, missing, or not in {Easy, Medium, Hard}. Equivalent to Easy; prevents
silent transition failures. An audit-comment note is appended to the Unit
when the fallback fires.
"""


class Settings(BaseSettings):
    """Runtime configuration loaded from the process environment.

    All fields are mandatory except ``aging_threshold_days`` which
    defaults to 90 (the LivingRequirements.md §5.2 T9 operational
    default; previously the ``StaleDays`` module constant). The runner
    fails fast at import time (via ``get_settings()``) if any required
    variable is missing or malformed; see docs/ExternalRunner.md §2.2
    for the canonical environment-variable contract.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    jira_url: HttpUrl = Field(
        ...,
        description="Base URL of the Jira Cloud / Server instance (https:// scheme).",
    )
    jira_user: str = Field(
        ...,
        description="Email address for Jira API Basic-auth (pairs with jira_token).",
    )
    jira_token: SecretStr = Field(
        ...,
        description="Jira API token; never logged, never surfaced in repr.",
    )
    jira_project_key: str = Field(
        ...,
        description="Target Jira project key, e.g. 'PROJ'; 2-10 upper-case letters.",
    )
    aging_threshold_days: int = Field(
        default=90,
        gt=0,
        description=(
            "T9 staleness threshold in calendar days. Units with no Sub-task "
            "activity older than this are candidates for the weekly stale "
            "scan (LivingRequirements.md §5.2 T9). Sole source of truth; "
            "environment override allowed for staging / test tuning."
        ),
    )

    @field_validator("jira_user")
    @classmethod
    def _jira_user_is_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@", 1)[1]:
            msg = f"jira_user must be an email address, got: {v!r}"
            raise ValueError(msg)
        return v

    @field_validator("jira_project_key")
    @classmethod
    def _jira_project_key_shape(cls, v: str) -> str:
        if not (2 <= len(v) <= 10) or not v.isalpha() or not v.isupper():
            msg = f"jira_project_key must be 2-10 upper-case letters, got: {v!r}"
            raise ValueError(msg)
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide ``Settings`` singleton.

    Parses the environment (and ``.env`` if present) on first call;
    subsequent calls return the cached instance. Raises
    ``pydantic.ValidationError`` on missing / malformed values - let it
    propagate so the runner crashes at startup rather than midway
    through a Jira write.
    """
    return Settings()  # type: ignore[call-arg]
