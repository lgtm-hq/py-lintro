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


#: An API-key variable no provider declares, used for override cases.
_RENAMED_KEY = "RENAMED_API_KEY"


#: The providers whose CLI reads the resolved API-key variable, pinned rather
#: than derived: a list filtered by ``honors_api_key_env`` would silently drop
#: a provider's cases if that flag were flipped, which is the very thing these
#: tests exist to lock. ``test_honouring_providers_match_the_metadata`` is what
#: ties the two together.
_API_KEY_HONOURING: tuple[AIProvider, ...] = (
    AIProvider.ANTHROPIC,
    AIProvider.CURSOR,
)


def test_honouring_providers_match_the_metadata() -> None:
    """The pinned list is exactly what the plugin records declare.

    Flipping ``honors_api_key_env`` on any provider fails here rather than
    quietly removing that provider's parametrized cases.
    """
    derived = tuple(
        provider
        for provider in AIProvider
        if metadata_for(provider).cli_auth_probe is not None
        and _probe(provider).honors_api_key_env
    )

    assert_that(derived).is_equal_to(_API_KEY_HONOURING)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip every credential the probes read so results are deterministic.

    The variables are read off the plugin metadata rather than copied, so a
    provider that starts reading a new one is stripped here without this
    fixture being edited.

    Args:
        monkeypatch: Pytest environment patcher.
        tmp_path: Empty directory used as the home directory.
    """
    monkeypatch.delenv(_RENAMED_KEY, raising=False)
    for provider in AIProvider:
        metadata = metadata_for(provider)
        monkeypatch.delenv(metadata.default_api_key_env, raising=False)
        probe = metadata.cli_auth_probe
        if probe is None:
            continue
        for name in probe.extra_env_vars:
            monkeypatch.delenv(name, raising=False)
    # `home` is a classmethod; patching it with a plain lambda would break the
    # `Path.home()` call the probe makes. HOME is set too so an `expanduser`
    # anywhere downstream cannot reach the real home directory either.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


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


@pytest.mark.parametrize("provider", _API_KEY_HONOURING)
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


@pytest.mark.parametrize("provider", _API_KEY_HONOURING)
def test_api_key_honouring_probes_accept_a_renamed_variable(
    provider: AIProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``honors_api_key_env`` means the *resolved* name, not the default one.

    ``ai.api_key_env`` renames the variable doctor reads, so the probe must
    accept whatever name it is handed rather than the provider default.

    Args:
        provider: The provider under test.
        monkeypatch: Pytest environment patcher.
    """
    probe = _probe(provider)
    monkeypatch.setenv(_RENAMED_KEY, "secret")

    assert_that(probe.is_configured(key_env=_RENAMED_KEY)).is_true()
    assert_that(probe.describe(key_env=_RENAMED_KEY)).contains(_RENAMED_KEY)
    assert_that(
        probe.is_configured(key_env=metadata_for(provider).default_api_key_env),
    ).is_false()


def test_probes_that_ignore_the_api_key_env_reject_a_renamed_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renamed SDK variable proves nothing for a CLI that never reads it.

    Args:
        monkeypatch: Pytest environment patcher.
    """
    monkeypatch.setenv(_RENAMED_KEY, "secret")

    assert_that(
        _probe(AIProvider.OPENAI).is_configured(key_env=_RENAMED_KEY),
    ).is_false()


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


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (
            AIProvider.ANTHROPIC,
            (
                "{key_env} set (API billing overrides subscription)",
                "Claude CLI auth not verified",
                "Run `claude login` or set ANTHROPIC_API_KEY",
            ),
        ),
        (
            AIProvider.OPENAI,
            (
                "Codex auth configured (CODEX_API_KEY or ~/.codex/auth.json)",
                "Codex CLI auth not verified",
                "Run `codex login` or set CODEX_API_KEY",
            ),
        ),
        (
            AIProvider.CURSOR,
            (
                "{key_env} is set",
                "Cursor CLI auth not verified",
                "Run `agent login` or set CURSOR_API_KEY",
            ),
        ),
    ],
)
def test_probe_messages_are_pinned_verbatim(
    provider: AIProvider,
    expected: tuple[str, str, str],
) -> None:
    """The user-visible probe strings are pinned, not read from the record.

    Doctor's tests compare its output against the metadata, which proves the
    wiring but not the wording. These literals are the pre-#2308 doctor
    messages, so a typo in a message shows up here rather than shipping.

    Args:
        provider: The provider under test.
        expected: The ``(configured, unverified, hint)`` triple it must carry.
    """
    probe = _probe(provider)

    assert_that(
        (probe.configured_message, probe.unverified_message, probe.hint),
    ).is_equal_to(expected)


def test_describe_leaves_a_message_without_a_placeholder_alone() -> None:
    """A probe whose message names no variable renders unchanged."""
    probe = _probe(AIProvider.OPENAI)

    assert_that(probe.describe(key_env="OPENAI_API_KEY")).is_equal_to(
        probe.configured_message,
    )
