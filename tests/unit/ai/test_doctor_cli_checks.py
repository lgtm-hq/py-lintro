"""Doctor's CLI-transport checks, now driven by plugin metadata (#2308).

``check_ai_configuration`` used to branch on provider identity for the install
hint, the auth verdict and the unsupported provider/transport pairing. All three
now read the configured provider's :class:`ProviderMetadata`, so these tests
assert the rendered results rather than the shape of the code that produces
them — the messages are the part users see and the part the migration promised
to preserve.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.doctor_checks import (
    AICheckResult,
    check_ai_configuration,
    check_ai_liveness,
)
from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_auth_probe import CliAuthProbe
from lintro.ai.registry import metadata_for
from lintro.enums.tool_status import ToolStatus

#: An API-key variable no provider declares, used for the override cases.
_RENAMED_KEY = "RENAMED_KEY"


def _honours_api_key_env(provider: AIProvider) -> bool:
    """Report whether the provider's CLI reads the resolved API-key variable.

    Args:
        provider: The provider to check.

    Returns:
        True when its probe sets ``honors_api_key_env``.
    """
    return _probe(provider).honors_api_key_env


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip every credential and binary doctor could otherwise find.

    The variables come from the plugin metadata rather than a hand-kept list,
    so a provider that starts reading a new one is stripped here too.

    Args:
        monkeypatch: Pytest environment patcher.
        tmp_path: Empty directory used as the home directory.
    """
    monkeypatch.delenv(_RENAMED_KEY, raising=False)
    # `is_transcript_enabled` honours this even when `AIConfig.transcript_logging`
    # is false, and doctor then prepends an `ai.transcript` result, which would
    # break the result-count assertions below on a machine that exports it.
    monkeypatch.delenv("LINTRO_AI_TRANSCRIPT", raising=False)
    for provider in AIProvider:
        metadata = metadata_for(provider)
        monkeypatch.delenv(metadata.default_api_key_env, raising=False)
        probe = metadata.cli_auth_probe
        if probe is None:
            continue
        for name in probe.extra_env_vars:
            monkeypatch.delenv(name, raising=False)
    # `home` is a classmethod, and HOME is set as well so an `expanduser`
    # anywhere downstream cannot reach the real home directory either.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _binary: None)


def _cli_config(
    provider: AIProvider,
    *,
    api_key_env: str | None = None,
) -> AIConfig:
    """Build an AI config that enables review over the CLI transport.

    Args:
        provider: Provider to configure.
        api_key_env: Optional ``ai.api_key_env`` override.

    Returns:
        The configuration doctor is asked about.
    """
    return AIConfig(
        enabled=True,
        review=True,
        provider=provider,
        transport=AITransport.CLI,
        api_key_env=api_key_env,
    )


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


def _result(results: list[AICheckResult], name: str) -> AICheckResult:
    """Return the single check result with *name*.

    Args:
        results: Results returned by doctor.
        name: Check name to select.

    Returns:
        The matching result.

    Raises:
        AssertionError: If no result carries that name.
    """
    for result in results:
        if result.name == name:
            return result
    raise AssertionError(f"no '{name}' check in {[r.name for r in results]}")


@pytest.mark.parametrize("provider", list(AIProvider))
def test_missing_cli_binary_reports_the_metadata_install_hint(
    provider: AIProvider,
) -> None:
    """A binary absent from PATH is MISSING, hinted from plugin metadata.

    Args:
        provider: The provider under test.
    """
    metadata = metadata_for(provider)
    results = check_ai_configuration(_cli_config(provider))
    result = _result(results, f"ai.cli.{metadata.cli_binary}")

    assert_that(result.status).is_equal_to(ToolStatus.MISSING)
    assert_that(result.message).contains(str(metadata.cli_binary))
    assert_that(result.hint).is_equal_to(metadata.cli_install_hint)


@pytest.mark.parametrize("provider", list(AIProvider))
def test_a_binary_on_path_is_reported_ok_with_its_location(
    provider: AIProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolvable binary is OK and the result names where it was found.

    The autouse fixture hides every binary, so without this case a doctor that
    ignored ``PATH`` and always reported MISSING would pass the file.

    Args:
        provider: The provider under test.
        monkeypatch: Pytest attribute patcher.
    """
    metadata = metadata_for(provider)
    found = f"/usr/local/bin/{metadata.cli_binary}"
    monkeypatch.setattr("shutil.which", lambda binary: f"/usr/local/bin/{binary}")

    results = check_ai_configuration(_cli_config(provider))
    result = _result(results, f"ai.cli.{metadata.cli_binary}")

    assert_that(result.status).is_equal_to(ToolStatus.OK)
    assert_that(result.message).contains(str(metadata.cli_binary), found)
    assert_that(result.hint).is_empty()


@pytest.mark.parametrize("provider", list(AIProvider))
def test_unproven_cli_auth_is_unknown_not_a_failure(provider: AIProvider) -> None:
    """No credential means doctor says "not verified", never "broken".

    Args:
        provider: The provider under test.
    """
    probe = _probe(provider)
    result = _result(check_ai_configuration(_cli_config(provider)), "ai.cli.auth")

    assert_that(result.status).is_equal_to(ToolStatus.UNKNOWN)
    assert_that(result.message).is_equal_to(probe.unverified_message)
    assert_that(result.hint).is_equal_to(probe.hint)


@pytest.mark.parametrize("provider", list(AIProvider))
def test_api_key_variable_marks_cli_auth_ok(
    provider: AIProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A set API-key variable is proof only for the CLIs that read it.

    Args:
        provider: The provider under test.
        monkeypatch: Pytest environment patcher.
    """
    key_env = metadata_for(provider).default_api_key_env
    monkeypatch.setenv(key_env, "secret")

    result = _result(check_ai_configuration(_cli_config(provider)), "ai.cli.auth")

    if not _honours_api_key_env(provider):
        # OpenAI's codex binary never reads OPENAI_API_KEY, so setting it must
        # leave the verdict unproven rather than reporting a false OK.
        assert_that(result.status).is_equal_to(ToolStatus.UNKNOWN)
        return
    assert_that(result.status).is_equal_to(ToolStatus.OK)
    assert_that(result.message).contains(key_env)


@pytest.mark.parametrize("provider", list(AIProvider))
def test_a_renamed_api_key_variable_is_honoured(
    provider: AIProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ai.api_key_env`` still overrides the variable doctor checks.

    This is pre-#2308 behaviour preserved verbatim. It is arguably wrong for
    Anthropic — the ``claude`` binary only reads ``ANTHROPIC_API_KEY`` — and
    that mismatch is left for #2449 rather than changed by a refactor.

    Args:
        provider: The provider under test.
        monkeypatch: Pytest environment patcher.
    """
    monkeypatch.setenv(_RENAMED_KEY, "secret")

    results = check_ai_configuration(
        _cli_config(provider, api_key_env=_RENAMED_KEY),
    )
    result = _result(results, "ai.cli.auth")

    if not _honours_api_key_env(provider):
        assert_that(result.status).is_equal_to(ToolStatus.UNKNOWN)
        return
    assert_that(result.status).is_equal_to(ToolStatus.OK)
    assert_that(result.message).contains(_RENAMED_KEY)


def test_a_renamed_api_key_env_cannot_prove_openai_cli_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An `ai.api_key_env` override must not reach a probe that ignores it.

    The `codex` binary authenticates with `CODEX_API_KEY` or `~/.codex/auth.json`
    and never reads an SDK variable, whatever it is named. This pins the override
    path specifically: every other OpenAI case leaves `ai.api_key_env` at its
    default, so only this one would catch a `key_env` that leaked past
    `honors_api_key_env`.

    Args:
        monkeypatch: Pytest environment patcher.
    """
    monkeypatch.setenv(_RENAMED_KEY, "secret")

    results = check_ai_configuration(
        _cli_config(AIProvider.OPENAI, api_key_env=_RENAMED_KEY),
    )
    result = _result(results, "ai.cli.auth")

    assert_that(result.status).is_equal_to(ToolStatus.UNKNOWN)
    assert_that(result.message).is_equal_to("Codex CLI auth not verified")


def test_doctor_never_hands_a_non_honouring_probe_an_api_key_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor resolves the API-key variable only for probes that read it.

    The verdict is already safe without this — ``is_configured`` guards on
    ``honors_api_key_env`` itself, which is why the test above cannot fail on
    the doctor line alone. This pins the other half of that defence: what doctor
    *passes*, so the two layers cannot quietly drift into relying on each other.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    seen: list[tuple[bool, str]] = []
    original = CliAuthProbe.is_configured

    def _spy(probe: CliAuthProbe, *, key_env: str) -> bool:
        seen.append((probe.honors_api_key_env, key_env))
        return original(probe, key_env=key_env)

    monkeypatch.setattr(CliAuthProbe, "is_configured", _spy)
    monkeypatch.setenv(_RENAMED_KEY, "secret")

    check_ai_configuration(_cli_config(AIProvider.OPENAI, api_key_env=_RENAMED_KEY))

    assert_that(seen).is_equal_to([(False, "")])


def test_openai_cli_auth_accepts_codex_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """CODEX_API_KEY, not OPENAI_API_KEY, is what the codex binary reads.

    Args:
        monkeypatch: Pytest environment patcher.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sdk-only")
    unproven = _result(
        check_ai_configuration(_cli_config(AIProvider.OPENAI)),
        "ai.cli.auth",
    )
    assert_that(unproven.status).is_equal_to(ToolStatus.UNKNOWN)

    monkeypatch.setenv("CODEX_API_KEY", "secret")
    proven = _result(
        check_ai_configuration(_cli_config(AIProvider.OPENAI)),
        "ai.cli.auth",
    )
    assert_that(proven.status).is_equal_to(ToolStatus.OK)
    assert_that(proven.message).contains("CODEX_API_KEY")


def test_openai_cli_auth_accepts_the_login_file(tmp_path: Path) -> None:
    """A ``codex login`` writes ~/.codex/auth.json and doctor accepts it.

    Args:
        tmp_path: Stand-in home directory installed by the fixture.
    """
    auth = tmp_path / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}", encoding="utf-8")

    result = _result(
        check_ai_configuration(_cli_config(AIProvider.OPENAI)),
        "ai.cli.auth",
    )

    assert_that(result.status).is_equal_to(ToolStatus.OK)
    assert_that(result.message).is_equal_to(
        "Codex auth configured (CODEX_API_KEY or ~/.codex/auth.json)",
    )


def test_unsupported_provider_transport_pairing_is_incompatible() -> None:
    """Cursor over the API transport is a configuration error, not a probe."""
    config = AIConfig(
        enabled=True,
        review=True,
        provider=AIProvider.CURSOR,
        transport=AITransport.API,
    )

    results = check_ai_configuration(config)
    result = _result(results, "ai.provider+transport")

    assert_that(results).is_length(1)
    assert_that(result.status).is_equal_to(ToolStatus.INCOMPATIBLE)
    assert_that(result.message).is_equal_to(
        "cursor provider only supports transport: cli",
    )
    assert_that(result.hint).is_equal_to(
        "Set `transport: cli` and install the Cursor agent CLI",
    )


def test_liveness_skips_an_unsupported_pairing_without_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No credential probe runs for a pairing that cannot be constructed.

    Asserting the empty result alone would also pass if the probe ran and
    returned nothing, so the probe itself is replaced by one that fails the
    test when called.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    calls: list[object] = []

    def _record(**kwargs: object) -> object:
        calls.append(kwargs)
        raise AssertionError("check_liveness_sync must not run for this pairing")

    monkeypatch.setattr(
        "lintro.ai.doctor_checks.check_liveness_sync",
        _record,
    )
    config = AIConfig(
        enabled=True,
        review=True,
        provider=AIProvider.CURSOR,
        transport=AITransport.API,
    )

    assert_that(check_ai_liveness(config)).is_empty()
    assert_that(calls).is_empty()
