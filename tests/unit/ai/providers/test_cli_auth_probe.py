"""Behaviour of the declarative CLI auth probe (#2308).

The probe replaced doctor's per-provider ``if`` ladder, so the rules that ladder
encoded — which variable counts, which login file counts, and which provider
honours a renamed ``ai.api_key_env`` — are asserted here on the real plugin
records rather than only as message strings.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_auth_probe import CliAuthProbe
from lintro.ai.registry import metadata_for


def _probe(provider: AIProvider) -> CliAuthProbe:
    """Return the declared CLI auth probe for *provider*.

    Args:
        provider: The provider to look up.

    Returns:
        The provider's auth probe.

    Raises:
        AssertionError: If the provider declares none.
    """
    probe = metadata_for(provider).cli_auth_probe
    if probe is None:
        raise AssertionError(f"{provider.value} declares no CLI auth probe")
    return probe


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip every credential the probes read so results are deterministic.

    Args:
        monkeypatch: Pytest environment patcher.
        tmp_path: Empty directory used as the home directory.
    """
    for name in ("ANTHROPIC_API_KEY", "CURSOR_API_KEY", "CODEX_API_KEY", "OTHER_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


@pytest.mark.parametrize("provider", list(AIProvider))
def test_probe_reports_unconfigured_without_any_credential(
    provider: AIProvider,
) -> None:
    """With nothing set, no provider's CLI is reported as authenticated.

    Args:
        provider: The provider under test.
    """
    metadata = metadata_for(provider)
    assert_that(
        _probe(provider).is_configured(key_env=metadata.default_api_key_env),
    ).is_false()


@pytest.mark.parametrize(
    "provider",
    [AIProvider.ANTHROPIC, AIProvider.CURSOR],
)
def test_api_key_honouring_probes_accept_the_resolved_variable(
    provider: AIProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A set API-key variable proves auth for the CLIs that read it.

    Args:
        provider: The provider under test.
        monkeypatch: Pytest environment patcher.
    """
    probe = _probe(provider)
    key_env = metadata_for(provider).default_api_key_env
    monkeypatch.setenv(key_env, "secret")

    assert_that(probe.is_configured(key_env=key_env)).is_true()
    assert_that(probe.describe(key_env=key_env)).contains(key_env)


def test_openai_probe_ignores_the_sdk_api_key_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENAI_API_KEY says nothing about the codex binary's own auth."""
    probe = _probe(AIProvider.OPENAI)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    assert_that(probe.is_configured(key_env="OPENAI_API_KEY")).is_false()


def test_openai_probe_accepts_its_own_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CODEX_API_KEY is what the codex binary reads."""
    monkeypatch.setenv("CODEX_API_KEY", "secret")

    assert_that(
        _probe(AIProvider.OPENAI).is_configured(key_env="OPENAI_API_KEY"),
    ).is_true()


def test_openai_probe_accepts_the_login_file(tmp_path: Path) -> None:
    """A codex login writes ~/.codex/auth.json, and that proves auth.

    Args:
        tmp_path: Stand-in home directory installed by the fixture.
    """
    auth = tmp_path / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}", encoding="utf-8")

    assert_that(
        _probe(AIProvider.OPENAI).is_configured(key_env="OPENAI_API_KEY"),
    ).is_true()


def test_describe_leaves_a_message_without_a_placeholder_alone() -> None:
    """A probe whose message names no variable renders unchanged."""
    probe = _probe(AIProvider.OPENAI)

    assert_that(probe.describe(key_env="OPENAI_API_KEY")).is_equal_to(
        probe.configured_message,
    )
