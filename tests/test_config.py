"""Parametric test suite for runner.config.Settings.

Covers the nine validation scenarios smoke-tested during M3.1 design:
happy path + default aging, three missing-required cases, malformed URL
(two shapes), non-email user, malformed project key (two shapes),
non-positive aging, singleton semantics, and SecretStr masking.

Every test uses monkeypatch to control the process environment and an
autouse fixture to wipe both the env and the ``get_settings`` lru_cache
between cases; no test leaks state into another.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from runner.config import Settings, get_settings

_ENV_KEYS = (
    "JIRA_URL",
    "JIRA_USER",
    "JIRA_TOKEN",
    "JIRA_PROJECT_KEY",
    "AGING_THRESHOLD_DAYS",
)

BASE_ENV: dict[str, str] = {
    "JIRA_URL": "https://example.atlassian.net",
    "JIRA_USER": "alice@example.com",
    "JIRA_TOKEN": "s3cret-token",
    "JIRA_PROJECT_KEY": "PROJ",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip inherited JIRA_* vars and reset the get_settings cache."""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _apply(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_happy_path_with_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, {**BASE_ENV, "AGING_THRESHOLD_DAYS": "14"})
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert str(s.jira_url) == "https://example.atlassian.net/"
    assert s.jira_user == "alice@example.com"
    assert s.jira_project_key == "PROJ"
    assert s.aging_threshold_days == 14
    assert s.jira_token.get_secret_value() == "s3cret-token"


def test_default_aging_threshold_is_90(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, BASE_ENV)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.aging_threshold_days == 90


def test_missing_required_fields_report_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, {"JIRA_URL": "https://example.atlassian.net"})
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # type: ignore[call-arg]
    reported = {".".join(str(p) for p in e["loc"]) for e in exc.value.errors()}
    assert {"jira_user", "jira_token", "jira_project_key"} <= reported


@pytest.mark.parametrize(
    ("overrides", "error_field", "error_fragment"),
    [
        pytest.param(
            {"JIRA_URL": "not-a-url"},
            "jira_url",
            "valid URL",
            id="malformed_url",
        ),
        pytest.param(
            {"JIRA_URL": "ftp://acme.example"},
            "jira_url",
            "http",
            id="wrong_scheme",
        ),
        pytest.param(
            {"JIRA_USER": "alice"},
            "jira_user",
            "email address",
            id="user_no_at_sign",
        ),
        pytest.param(
            {"JIRA_USER": "alice@nodot"},
            "jira_user",
            "email address",
            id="user_no_tld_dot",
        ),
        pytest.param(
            {"JIRA_PROJECT_KEY": "proj"},
            "jira_project_key",
            "upper-case",
            id="lowercase_key",
        ),
        pytest.param(
            {"JIRA_PROJECT_KEY": "PROJECTNAMEWAYTOOLONG"},
            "jira_project_key",
            "upper-case",
            id="overlong_key",
        ),
        pytest.param(
            {"AGING_THRESHOLD_DAYS": "0"},
            "aging_threshold_days",
            "greater than 0",
            id="aging_zero",
        ),
        pytest.param(
            {"AGING_THRESHOLD_DAYS": "-1"},
            "aging_threshold_days",
            "greater than 0",
            id="aging_negative",
        ),
    ],
)
def test_settings_field_validation(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    error_field: str,
    error_fragment: str,
) -> None:
    _apply(monkeypatch, {**BASE_ENV, **overrides})
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # type: ignore[call-arg]
    matches = [e for e in exc.value.errors() if ".".join(str(p) for p in e["loc"]) == error_field]
    assert matches, f"expected error on {error_field}, got {exc.value.errors()}"
    assert any(error_fragment in e["msg"] for e in matches)


def test_get_settings_is_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, BASE_ENV)
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_secret_str_masks_token_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _apply(monkeypatch, {**BASE_ENV, "JIRA_TOKEN": "leak-canary-xyz"})
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "leak-canary-xyz" not in repr(s)
    assert "**********" in repr(s)
    assert s.jira_token.get_secret_value() == "leak-canary-xyz"
