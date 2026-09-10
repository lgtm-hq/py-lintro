"""Tests for provider-specific settings nested under ``ai.providers`` (#2309).

One test per acceptance criterion, plus the precedence and diagnostic
behaviour those criteria depend on:

1. no top-level ``AIConfig`` field is consumed by a single provider;
2. nested fields resolve on the flag / env / project / user layers with
   per-field provenance;
3. the legacy top-level spellings warn once per run, with pinned text;
4. ``docs/ai-features.md`` documents the nested block and the env-var shape.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import click
import pytest
import yaml
from assertpy import assert_that
from click.testing import CliRunner
from loguru import logger
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.ai.config_overrides import (
    _ENABLED_ACCEPTED,
    ENV_PROVIDER_BLOCK_PREFIX,
)
from lintro.ai.doctor_checks import check_ai_configuration
from lintro.ai.effective_config import AICliOverrides, resolve_effective_ai_config
from lintro.ai.enums import CliBareMode
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.provider_blocks import nested_source_key
from lintro.ai.provider_config import (
    LEGACY_KEY_REMOVAL_ISSUE,
    ProviderConfig,
    legacy_key_field_paths,
    legacy_key_warning,
    reset_legacy_key_warnings,
)
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.anthropic.config import AnthropicConfig, anthropic_settings
from lintro.ai.providers.cursor.config import CursorConfig, cursor_settings
from lintro.ai.providers.openai.config import OpenAIConfig, openai_settings
from lintro.ai.registry import all_metadata, config_model_for, provider_config_models
from lintro.cli import cli
from lintro.cli_utils.commands.config_ai import print_ai_config
from lintro.cli_utils.commands.review import _parse_provider_options
from lintro.config import LintroConfig
from lintro.config.config_loader import clear_config_cache

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGE_ROOT = _REPO_ROOT / "lintro"
_PROVIDERS_ROOT = _PACKAGE_ROOT / "ai" / "providers"

#: Modules that only *declare* the flat fields or name them as legacy
#: spellings. A mention here is not evidence that anything consumes the field,
#: so the AC1 guard ignores them when deciding whether a field has a shared
#: consumer.
_DECLARATION_ONLY = frozenset(
    {
        _PACKAGE_ROOT / "ai" / "config.py",
        _PACKAGE_ROOT / "ai" / "provider_config.py",
    },
)


@dataclass(frozen=True)
class _FakeLintroConfig:
    """The one attribute :func:`print_ai_config` reads off a loaded config.

    Attributes:
        ai: Raw ``ai:`` mapping, exactly as the loader stores it.
    """

    ai: dict[str, Any] = field(default_factory=dict)


_TRUST_KEY = nested_source_key(
    provider=AIProvider.CURSOR,
    field="trust_workspace",
)
_ENV_TRUST = f"{ENV_PROVIDER_BLOCK_PREFIX}CURSOR__TRUST_WORKSPACE"


@pytest.fixture
def isolated_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Return an empty project directory with the user tier isolated.

    The loader deep-merges ``~/.lintro-config.yaml`` into the project config,
    so a contributor's own AI settings would otherwise reach every test that
    goes through ``load_config``.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The project directory, already the working directory.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("LINTRO_GLOBAL_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    clear_config_cache()
    return project


@pytest.fixture(autouse=True)
def _rearm_legacy_warnings() -> Iterator[None]:
    """Re-arm the once-per-run legacy warning around every test.

    Yields:
        None: For the duration of the test.
    """
    reset_legacy_key_warnings()
    yield
    reset_legacy_key_warnings()


# -- AC1: no single-provider field survives on AIConfig --------------------


def _provider_package_sources() -> dict[AIProvider, str]:
    """Read every in-tree provider package as one text blob per provider.

    Returns:
        Concatenated package source keyed by provider.
    """
    sources: dict[AIProvider, str] = {}
    for provider in all_metadata():
        package = _PROVIDERS_ROOT / provider.value
        sources[provider] = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(package.rglob("*.py"))
        )
    return sources


def _shared_sources() -> str:
    """Read every module that is not inside a provider package.

    Returns:
        Concatenated source of the shared pipeline, with the modules that only
        declare the fields excluded.
    """
    package_dirs = {_PROVIDERS_ROOT / provider.value for provider in all_metadata()}
    chunks: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        if path in _DECLARATION_ONLY:
            continue
        if any(directory in path.parents for directory in package_dirs):
            continue
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_no_top_level_field_belongs_to_a_single_provider() -> None:
    """Every flat ``AIConfig`` field is shared, not one vendor's private knob.

    A field mentioned by exactly one provider package and by nothing in the
    shared pipeline is the shape #2309 exists to prevent: it reads as a global
    setting but means nothing for the other providers. The remedy is to move it
    into that provider's ``ai.providers.<name>`` block, not to widen this test.

    The evidence is a name match over source text, so it is a lower bound: a
    field named in a comment or docstring counts as a mention, and a field no
    one reads at all passes. That is deliberate — the alternative is modelling
    attribute access, which a plugin can defeat with ``getattr`` — so the
    guard catches the regression it is aimed at (a new vendor knob landing on
    the flat model) and does not pretend to prove consumption.
    """
    provider_sources = _provider_package_sources()
    shared = _shared_sources()

    offenders: dict[str, str] = {}
    for field_name in AIConfig.model_fields:
        if field_name == "providers":
            continue
        pattern = re.compile(rf"\b{re.escape(field_name)}\b")
        owners = [
            provider.value
            for provider, source in provider_sources.items()
            if pattern.search(source)
        ]
        if len(owners) == 1 and not pattern.search(shared):
            offenders[field_name] = owners[0]

    assert_that(offenders).described_as(
        "top-level ai fields consumed by exactly one provider",
    ).is_equal_to({})


def test_every_plugin_declares_a_block_model() -> None:
    """The plugin protocol, not a central table, says who owns which keys."""
    models = provider_config_models()

    assert_that(sorted(provider.value for provider in models)).is_equal_to(
        sorted(provider.value for provider in all_metadata()),
    )
    for model in models.values():
        assert_that(issubclass(model, ProviderConfig)).is_true()


def test_the_moved_knobs_live_on_their_own_provider_block() -> None:
    """The two migrated knobs are declared by exactly one provider each."""
    assert_that(sorted(CursorConfig.model_fields)).is_equal_to(["trust_workspace"])
    assert_that(sorted(AnthropicConfig.model_fields)).is_equal_to(["cli_bare"])
    assert_that(sorted(OpenAIConfig.model_fields)).is_equal_to([])
    assert_that("cursor_trust_workspace" in AIConfig.model_fields).is_false()
    assert_that("cli_bare" in AIConfig.model_fields).is_false()


# -- AC2: provenance across flag / env / project / user --------------------


def test_nested_field_default_provenance() -> None:
    """An untouched nested field resolves to its model default."""
    resolved = resolve_effective_ai_config({"provider": "cursor"})

    assert_that(cursor_settings(resolved.config).trust_workspace).is_true()
    assert_that(resolved.sources[_TRUST_KEY]).is_equal_to(ConfigSource.DEFAULT)


def test_nested_field_project_provenance() -> None:
    """A value written under ``ai.providers`` is reported as config."""
    resolved = resolve_effective_ai_config(
        {"provider": "cursor", "providers": {"cursor": {"trust_workspace": False}}},
    )

    assert_that(cursor_settings(resolved.config).trust_workspace).is_false()
    assert_that(resolved.sources[_TRUST_KEY]).is_equal_to(ConfigSource.CONFIG)


def test_nested_field_env_beats_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LINTRO_AI_PROVIDERS__CURSOR__TRUST_WORKSPACE`` overrides the file.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(_ENV_TRUST, "true")

    resolved = resolve_effective_ai_config(
        {"provider": "cursor", "providers": {"cursor": {"trust_workspace": False}}},
    )

    assert_that(cursor_settings(resolved.config).trust_workspace).is_true()
    assert_that(resolved.sources[_TRUST_KEY]).is_equal_to(ConfigSource.ENV)


def test_nested_field_flag_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--provider-option`` outranks the environment variable.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(_ENV_TRUST, "true")

    resolved = resolve_effective_ai_config(
        {"provider": "cursor", "providers": {"cursor": {"trust_workspace": True}}},
        cli_overrides=AICliOverrides(
            provider_options={"trust_workspace": "false"},
        ),
    )

    assert_that(cursor_settings(resolved.config).trust_workspace).is_false()
    assert_that(resolved.sources[_TRUST_KEY]).is_equal_to(ConfigSource.FLAG)


def test_nested_field_from_the_user_global_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user-level ``~/.lintro-config.yaml`` supplies the nested value.

    The user tier reaches the resolver through the loader's deep merge, so a
    nested block set once in the home file applies to every project that does
    not override it — and a project that does override it wins.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.config.config_loader import load_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("LINTRO_GLOBAL_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".lintro-config.yaml").write_text(
        yaml.safe_dump(
            {"ai": {"providers": {"cursor": {"trust_workspace": False}}}},
        ),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".lintro-config.yaml").write_text(
        yaml.safe_dump({"ai": {"provider": "cursor"}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    clear_config_cache()

    resolved = resolve_effective_ai_config(load_config().ai)

    assert_that(cursor_settings(resolved.config).trust_workspace).is_false()
    assert_that(resolved.sources[_TRUST_KEY]).is_equal_to(ConfigSource.CONFIG)

    (project / ".lintro-config.yaml").write_text(
        yaml.safe_dump(
            {
                "ai": {
                    "provider": "cursor",
                    "providers": {"cursor": {"trust_workspace": True}},
                },
            },
        ),
        encoding="utf-8",
    )
    clear_config_cache()

    overridden = resolve_effective_ai_config(load_config().ai)

    assert_that(cursor_settings(overridden.config).trust_workspace).is_true()
    clear_config_cache()


def test_one_overlay_leaves_the_rest_of_the_block_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An overlay sets one field; another provider's block is untouched.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(_ENV_TRUST, "false")

    resolved = resolve_effective_ai_config(
        {
            "provider": "cursor",
            "providers": {"anthropic": {"cli_bare": "never"}},
        },
    )

    assert_that(cursor_settings(resolved.config).trust_workspace).is_false()
    assert_that(anthropic_settings(resolved.config).cli_bare).is_equal_to(
        CliBareMode.NEVER,
    )


# -- Diagnostics -----------------------------------------------------------


def test_unknown_block_field_is_rejected_where_it_was_written() -> None:
    """A key the provider does not declare fails, naming the full path.

    ``workspace_trust`` is the plausible-but-wrong spelling of Cursor's
    ``trust_workspace`` — a real word pair rather than a misspelling, so the
    repository spell-checker cannot quietly "fix" this fixture into a valid
    key and neuter the test.
    """
    with pytest.raises(ValueError) as excinfo:
        AIConfig(
            providers={"cursor": {"workspace_trust": False}},  # type: ignore[dict-item]
        )

    assert_that(str(excinfo.value)).contains("ai.providers.cursor.workspace_trust")


@pytest.mark.parametrize("block", [None, {}])
def test_an_empty_provider_block_means_all_defaults(
    block: dict[str, Any] | None,
) -> None:
    """``cursor:`` written empty is "this provider, all defaults", not an error.

    Both spellings a user reaches for reach the validator differently: YAML
    parses a key with nothing under it as ``None``, while an explicit ``{}``
    arrives as an empty mapping.

    Args:
        block: The empty block spelling under test.
    """
    config = AIConfig(
        provider=AIProvider.CURSOR,
        providers={AIProvider.CURSOR: block},  # type: ignore[dict-item]
    )

    settings = cursor_settings(config)
    assert_that(settings).is_instance_of(CursorConfig)
    assert_that(settings.trust_workspace).is_equal_to(
        CursorConfig().trust_workspace,
    )


def test_an_empty_providers_section_means_no_blocks() -> None:
    """``providers:`` with nothing under it is an empty section, not a failure.

    An empty inner block already normalizes to "all defaults", so the empty
    container must not be the one spelling that hard-fails the run. A present
    non-mapping that is not null is still rejected.
    """
    config = AIConfig.model_validate({"provider": "cursor", "providers": None})

    assert_that(config.providers).is_equal_to({})
    assert_that(cursor_settings(config).trust_workspace).is_equal_to(
        CursorConfig().trust_workspace,
    )

    with pytest.raises(ValueError):
        AIConfig.model_validate({"provider": "cursor", "providers": "cursor"})


def test_unknown_provider_block_is_dropped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A block for a provider lintro has never heard of does not break the run.

    Deliberately asymmetric with the override layers, which hard-fail on the
    same input (see ``test_env_override_names_an_unknown_provider``). A stale
    key in a committed config file must not break every run — that is how
    unrecognized top-level ``ai:`` keys already behave — whereas an override
    typed for this invocation silently doing nothing is worse than one that
    stops the run.

    Args:
        caplog: Pytest log capture fixture.
    """
    handler_id = logger.add(caplog.handler, format="{message}")
    try:
        config = AIConfig(
            providers={"llamatron": {"anything": 1}},  # type: ignore[dict-item]
        )
    finally:
        logger.remove(handler_id)

    assert_that(config.providers).is_equal_to({})
    assert_that(caplog.text).contains("ai.providers.llamatron")


def test_env_override_names_an_unknown_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown field fails at resolution listing what the provider accepts.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(f"{ENV_PROVIDER_BLOCK_PREFIX}CURSOR__NOPE", "1")

    with pytest.raises(AIConfigOverrideError) as excinfo:
        resolve_effective_ai_config({})

    message = str(excinfo.value)
    assert_that(message).contains(f"{ENV_PROVIDER_BLOCK_PREFIX}CURSOR__NOPE")
    assert_that(message).contains("trust_workspace")


def test_env_override_names_an_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown provider segment is an error, not a silent no-op.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(f"{ENV_PROVIDER_BLOCK_PREFIX}LLAMATRON__X", "1")

    with pytest.raises(AIConfigOverrideError) as excinfo:
        resolve_effective_ai_config({})

    assert_that(str(excinfo.value)).contains("llamatron")


def test_env_override_rejects_a_bad_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value the block model rejects never falls through to the default.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(_ENV_TRUST, "maybe")

    with pytest.raises(AIConfigOverrideError) as excinfo:
        resolve_effective_ai_config({})

    # The accepted-values text is lintro's, shared with the flat overrides;
    # importing it keeps this from pinning a private spelling of the same list.
    assert_that(str(excinfo.value)).contains(_ENABLED_ACCEPTED)
    assert_that(str(excinfo.value)).contains(_ENV_TRUST)


def test_provider_option_flag_without_a_provider_is_an_error() -> None:
    """``--provider-option`` needs to know which provider it is talking to."""
    with pytest.raises(AIConfigOverrideError) as excinfo:
        resolve_effective_ai_config(
            {},
            cli_overrides=AICliOverrides(
                provider_options={"trust_workspace": "false"},
            ),
        )

    assert_that(str(excinfo.value)).contains("--provider-option")


def test_provider_option_flag_binds_to_the_provider_flag() -> None:
    """``--provider`` in the same invocation decides whose block is written."""
    resolved = resolve_effective_ai_config(
        {"provider": "anthropic"},
        cli_overrides=AICliOverrides(
            provider="cursor",
            provider_options={"trust_workspace": "false"},
        ),
    )

    assert_that(cursor_settings(resolved.config).trust_workspace).is_false()
    assert_that(anthropic_settings(resolved.config).cli_bare).is_equal_to(
        CliBareMode.AUTO,
    )


# -- The --provider-option flag parser -------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("trust_workspace=false",), {"trust_workspace": "false"}),
        (("a=1", "b=2"), {"a": "1", "b": "2"}),
        # A repeated name keeps the last value, like every other override.
        (("a=1", "a=2"), {"a": "2"}),
        # Only the first '=' separates; the rest belongs to the value.
        (("a=b=c",), {"a": "b=c"}),
        # An empty value is a value: emptiness is the block model's problem.
        (("a=",), {"a": ""}),
        ((" a =1",), {"a": "1"}),
        ((), {}),
    ],
)
def test_provider_option_flag_parsing(
    values: tuple[str, ...],
    expected: dict[str, str],
) -> None:
    """The flag splits on the first ``=`` and holds no per-vendor knowledge.

    Args:
        values: Raw ``--provider-option`` values as Click collects them.
        expected: The mapping handed to the resolver.
    """
    assert_that(_parse_provider_options(values=values)).is_equal_to(expected)


@pytest.mark.parametrize("value", ["trust_workspace", "=false", " =false", ""])
def test_provider_option_flag_rejects_a_malformed_pair(value: str) -> None:
    """A value that is not ``NAME=VALUE`` is a usage error, not a silent skip.

    Args:
        value: The malformed flag value.
    """
    with pytest.raises(click.UsageError) as excinfo:
        _parse_provider_options(values=(value,))

    assert_that(str(excinfo.value)).contains("NAME=VALUE")


def test_review_rejects_an_unknown_provider_option() -> None:
    """``lintro review`` surfaces the resolver's rejection as a usage error.

    ``require_ai`` and ``get_config`` are patched the way every other
    ``lintro review`` test patches them: the guard runs before the overrides
    are resolved, so without the patch this asserts on "AI features require
    lintro[ai]" on the matrix leg installed without the extra, and never
    reaches the validation it exists to cover.
    """
    mock_config = MagicMock(
        ai={"enabled": True, "review": True, "provider": "cursor"},
    )
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
    ):
        result = CliRunner().invoke(
            cli,
            ["review", "--provider-option", "trust_workspaces=false"],
        )

    assert_that(result.exit_code).is_equal_to(2)
    output = " ".join(result.output.split())
    # ``trust_workspace`` is a substring of the typed ``trust_workspaces``, so
    # only the accepted-names phrase proves the error lists what is settable
    # rather than merely echoing the input back.
    assert_that(output).contains("has no setting 'trust_workspaces'")
    assert_that(output).contains("accepted: trust_workspace")
    assert_that(output).does_not_contain("Traceback")


# -- AC3: the legacy shim --------------------------------------------------


def test_legacy_key_is_still_honoured() -> None:
    """A legacy top-level key still reaches the provider that reads it."""
    resolved = resolve_effective_ai_config(
        {"provider": "cursor", "cursor_trust_workspace": False},
    )

    assert_that(cursor_settings(resolved.config).trust_workspace).is_false()
    assert_that(resolved.sources[_TRUST_KEY]).is_equal_to(ConfigSource.CONFIG)


def test_anthropic_legacy_key_is_still_honoured() -> None:
    """``ai.cli_bare`` still reaches the Anthropic block.

    Anthropic's mapping is the identity case — the legacy key and the nested
    field share a name — so it exercises the migration's pop/setdefault
    sequence differently from cursor's renamed key.
    """
    resolved = resolve_effective_ai_config(
        {"provider": "anthropic", "cli_bare": "never"},
    )

    assert_that(anthropic_settings(resolved.config).cli_bare).is_equal_to(
        CliBareMode.NEVER,
    )
    assert_that(
        resolved.sources[
            nested_source_key(provider=AIProvider.ANTHROPIC, field="cli_bare")
        ],
    ).is_equal_to(ConfigSource.CONFIG)


@pytest.mark.parametrize(
    ("legacy_key", "path"),
    sorted(legacy_key_field_paths().items()),
)
def test_every_declared_legacy_key_migrates_and_warns(
    legacy_key: str,
    path: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No provider may declare a legacy spelling the shim does not honour.

    Parametrised over the registry rather than a hand-written list, so a
    provider that adds a legacy key gets this coverage for free — and the
    removal issue (#2464) has one place to delete.

    Args:
        legacy_key: The top-level spelling a provider still accepts.
        path: The ``providers.<name>.<field>`` path it maps to.
        caplog: Pytest log capture fixture.
    """
    _, provider_name, block_field = path.split(".")
    model = config_model_for(provider_name)
    default = getattr(model(), block_field)

    handler_id = logger.add(caplog.handler, format="{message}")
    try:
        resolved = resolve_effective_ai_config({legacy_key: default})
    finally:
        logger.remove(handler_id)

    settings = resolved.config.provider_settings(AIProvider(provider_name))
    assert_that(getattr(settings, block_field)).is_equal_to(default)
    assert_that(resolved.sources[path]).is_equal_to(ConfigSource.CONFIG)
    assert_that(caplog.text).contains(f"ai.{legacy_key} is deprecated")
    assert_that(caplog.text).contains(f"ai.{path}")


def test_legacy_key_warning_text_names_the_new_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The deprecation says exactly where to move the key, and why.

    Args:
        caplog: Pytest log capture fixture.
    """
    handler_id = logger.add(caplog.handler, format="{message}")
    try:
        resolve_effective_ai_config({"cursor_trust_workspace": False})
    finally:
        logger.remove(handler_id)

    assert_that(caplog.text).contains(
        "ai.cursor_trust_workspace is deprecated and will be removed in a "
        f"future release (#{LEGACY_KEY_REMOVAL_ISSUE}); move it to "
        "ai.providers.cursor.trust_workspace.",
    )


def test_legacy_key_warning_is_built_once_and_logged_verbatim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the run logs is exactly what the builder produced.

    The prose itself is pinned once, in
    :func:`test_legacy_key_warning_text_names_the_new_path`. Copying it here
    too would mean a rewording had to be applied in two places to stay green,
    which is the drift this asserts against — so this checks the property
    (one builder, no second copy) rather than the text.

    Args:
        caplog: Pytest log capture fixture.
    """
    handler_id = logger.add(caplog.handler, format="{message}")
    try:
        resolve_effective_ai_config({"cli_bare": "never"})
    finally:
        logger.remove(handler_id)

    assert_that(caplog.messages).contains(
        legacy_key_warning(
            legacy_key="cli_bare",
            provider="anthropic",
            field="cli_bare",
        ),
    )


def test_legacy_key_warns_once_per_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Re-resolving the same mapping does not repeat the deprecation.

    Display surfaces resolve the config again to render it; a warning per
    resolution would be a wall of duplicates for one stale key.

    Args:
        caplog: Pytest log capture fixture.
    """
    handler_id = logger.add(caplog.handler, format="{message}")
    try:
        for _ in range(3):
            resolve_effective_ai_config({"cursor_trust_workspace": False})
    finally:
        logger.remove(handler_id)

    assert_that(
        caplog.text.count("ai.cursor_trust_workspace is deprecated"),
    ).is_equal_to(
        1,
    )


def test_nested_value_wins_over_the_legacy_key() -> None:
    """The shim is a fallback, never an override of the new spelling."""
    resolved = resolve_effective_ai_config(
        {
            "cursor_trust_workspace": True,
            "providers": {"cursor": {"trust_workspace": False}},
        },
    )

    assert_that(cursor_settings(resolved.config).trust_workspace).is_false()


def test_a_real_typo_is_still_reported_as_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Accepting legacy keys did not turn every stale key into a legacy one.

    ``cursor_workspace_trust`` is a plausible-but-wrong *reordering* of the
    legacy ``cursor_trust_workspace`` rather than a misspelling, so the
    repository spell-checker cannot rewrite this fixture into the very key it
    must not match.

    Args:
        caplog: Pytest log capture fixture.
    """
    handler_id = logger.add(caplog.handler, format="{message}")
    try:
        resolve_effective_ai_config({"cursor_workspace_trust": False})
    finally:
        logger.remove(handler_id)

    assert_that(caplog.text).contains("Unknown AI config keys ignored")


# -- Display surfaces ------------------------------------------------------


def test_lintro_config_shows_only_the_selected_provider_block(
    isolated_project: Path,
) -> None:
    """``lintro config`` renders one block plus a count of the others.

    Args:
        isolated_project: Empty project directory with the user tier isolated.
    """
    (isolated_project / ".lintro-config.yaml").write_text(
        yaml.safe_dump(
            {
                "ai": {
                    "provider": "cursor",
                    "transport": "cli",
                    "providers": {
                        "cursor": {"trust_workspace": False},
                        "anthropic": {"cli_bare": "never"},
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    clear_config_cache()

    result = CliRunner().invoke(cli, ["config"])

    clear_config_cache()
    assert_that(result.exit_code).is_equal_to(0)
    output = " ".join(result.output.split())
    assert_that(output).contains("providers.cursor.trust_workspace")
    assert_that(output).does_not_contain("cli_bare")
    assert_that(output).contains("1 other provider block configured")


def test_lintro_config_json_carries_the_same_block(
    isolated_project: Path,
) -> None:
    """``--json`` reports the block the rich section shows, with provenance.

    Args:
        isolated_project: Empty project directory with the user tier isolated.
    """
    (isolated_project / ".lintro-config.yaml").write_text(
        yaml.safe_dump(
            {
                "ai": {
                    "provider": "cursor",
                    "providers": {
                        "cursor": {"trust_workspace": False},
                        "anthropic": {"cli_bare": "never"},
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    clear_config_cache()

    result = CliRunner().invoke(cli, ["config", "--json"])

    clear_config_cache()
    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)["ai"]
    assert_that(payload["provider"]).is_equal_to("cursor")
    assert_that(payload["provider_settings"]).is_equal_to(
        {"trust_workspace": {"value": False, "source": "config"}},
    )
    assert_that(payload["other_provider_blocks"]).is_equal_to(1)


def test_lintro_config_nudges_a_legacy_key_once(
    isolated_project: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``lintro config`` reports the legacy spelling it silently migrated.

    This report runs no execution path afterwards, so suppressing the
    deprecation here would leave the shim invisible for the whole invocation.

    Args:
        isolated_project: Empty project directory with the user tier isolated.
        caplog: Pytest log capture fixture.
    """
    (isolated_project / ".lintro-config.yaml").write_text(
        yaml.safe_dump({"ai": {"provider": "cursor", "cursor_trust_workspace": False}}),
        encoding="utf-8",
    )
    clear_config_cache()

    handler_id = logger.add(caplog.handler, format="{message}")
    try:
        result = CliRunner().invoke(cli, ["config"])
    finally:
        logger.remove(handler_id)
        clear_config_cache()

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(
        caplog.text.count("ai.cursor_trust_workspace is deprecated"),
    ).is_equal_to(1)


def test_lintro_config_survives_an_invalid_provider_block(
    isolated_project: Path,
) -> None:
    """A bad block costs the AI section, not the whole report.

    ``lintro config`` is what a user runs to diagnose a bad config, so it must
    still render the sections after the AI one.

    Args:
        isolated_project: Empty project directory with the user tier isolated.
    """
    (isolated_project / ".lintro-config.yaml").write_text(
        yaml.safe_dump(
            {
                "ai": {
                    "provider": "cursor",
                    "providers": {"cursor": {"trust_workspace": "maybe"}},
                },
            },
        ),
        encoding="utf-8",
    )
    clear_config_cache()

    result = CliRunner().invoke(cli, ["config"])

    clear_config_cache()
    assert_that(result.exit_code).is_equal_to(0)
    output = " ".join(result.output.split())
    assert_that(output).contains("ai.providers.cursor.trust_workspace")
    assert_that(output).contains("Tool Execution Order")


def test_ai_config_section_names_a_provider_with_no_settings() -> None:
    """A provider whose block model is empty says so rather than rendering none."""
    console = Console(record=True, width=100)

    print_ai_config(
        console=console,
        config=cast(LintroConfig, _FakeLintroConfig({"provider": "openai"})),
    )

    text = " ".join(console.export_text().split())
    assert_that(text).contains("providers.openai")
    assert_that(text).contains("no provider-specific settings")
    assert_that(text).does_not_contain("other provider block")


def test_ai_config_section_reports_no_provider() -> None:
    """With no provider selected the shared rows still render."""
    console = Console(record=True, width=100)

    print_ai_config(
        console=console,
        config=cast(LintroConfig, _FakeLintroConfig({})),
    )

    text = " ".join(console.export_text().split())
    assert_that(text).contains("provider unset")


def test_doctor_reports_the_selected_provider_block() -> None:
    """Doctor names the non-default settings of the provider in use."""
    config = AIConfig(
        enabled=True,
        lint=True,
        provider=AIProvider.CURSOR,
        transport="cli",  # type: ignore[arg-type]  # Pydantic coerces str
        providers={AIProvider.CURSOR: CursorConfig(trust_workspace=False)},
    )

    names = {check.name: check.message for check in check_ai_configuration(config)}

    assert_that(names).contains_key("ai.providers.cursor")
    assert_that(names["ai.providers.cursor"]).contains("trust_workspace=False")


def test_doctor_stays_quiet_when_the_block_is_all_defaults() -> None:
    """A default-valued block is not worth a doctor row."""
    config = AIConfig(
        enabled=True,
        lint=True,
        provider=AIProvider.CURSOR,
        transport="cli",  # type: ignore[arg-type]  # Pydantic coerces str
    )

    names = [check.name for check in check_ai_configuration(config)]

    assert_that(names).does_not_contain("ai.providers.cursor")


# -- Accessors -------------------------------------------------------------


def test_provider_settings_defaults_to_the_selected_provider() -> None:
    """``provider_settings()`` reads the configured provider's block."""
    config = AIConfig(
        provider=AIProvider.ANTHROPIC,
        providers={AIProvider.ANTHROPIC: AnthropicConfig(cli_bare=CliBareMode.NEVER)},
    )

    assert_that(config.provider_settings()).is_equal_to(
        AnthropicConfig(cli_bare=CliBareMode.NEVER),
    )


def test_provider_settings_synthesises_a_missing_block() -> None:
    """An unwritten block reads as that provider's defaults, never None."""
    config = AIConfig(provider=AIProvider.OPENAI)

    assert_that(openai_settings(config)).is_equal_to(OpenAIConfig())
    assert_that(config.provider_settings(AIProvider.CURSOR)).is_equal_to(
        CursorConfig(),
    )
    assert_that(config.other_provider_block_count()).is_equal_to(0)


def test_blocks_survive_a_dump_and_revalidate_round_trip() -> None:
    """Overlays copy the config through pydantic; nested fields must survive.

    The ``providers`` field is annotated with the base class, so without
    ``SerializeAsAny`` a dump would silently drop every vendor's own keys and
    an env overlay would reset the block to its defaults.
    """
    config = AIConfig(
        provider=AIProvider.CURSOR,
        providers={AIProvider.CURSOR: CursorConfig(trust_workspace=False)},
    )

    payload: dict[str, Any] = config.model_dump()
    round_tripped = AIConfig.model_validate(payload)

    assert_that(cursor_settings(round_tripped).trust_workspace).is_false()


def test_config_model_for_accepts_the_string_a_user_typed() -> None:
    """Block lookup normalises the provider name like every other lookup."""
    assert_that(config_model_for("cursor")).is_same_as(CursorConfig)


# -- AC4: documentation ----------------------------------------------------


def test_docs_document_the_nested_block_and_the_env_shape() -> None:
    """``docs/ai-features.md`` explains the block, the env var and the shim."""
    text = (_REPO_ROOT / "docs" / "ai-features.md").read_text(encoding="utf-8")

    assert_that(text).contains("### Provider-specific settings")
    assert_that(text).contains("ai.providers.cursor.trust_workspace")
    assert_that(text).contains("ai.providers.anthropic.cli_bare")
    assert_that(text).contains("LINTRO_AI_PROVIDERS__CURSOR__TRUST_WORKSPACE")
    assert_that(text).contains("--provider-option")
    assert_that(text).contains(str(LEGACY_KEY_REMOVAL_ISSUE))


def test_configuration_docs_list_the_block_env_variable() -> None:
    """The env-var reference table carries the block override shape."""
    text = (_REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")

    assert_that(text).contains("LINTRO_AI_PROVIDERS__<PROVIDER>__<FIELD>")


def test_a_block_given_as_another_providers_model_is_rejected() -> None:
    """A model instance stored under the wrong key fails instead of being ignored.

    A ``ProviderConfig`` instance bypasses the per-provider validation, so
    ``providers={cursor: AnthropicConfig(...)}`` used to be stored verbatim and
    then dropped by ``cursor_settings()``, which returns Cursor's defaults for
    anything that is not a ``CursorConfig``. Silently ignoring a block the user
    wrote is worse than refusing it.
    """
    with pytest.raises(ValueError) as excinfo:
        AIConfig(
            provider=AIProvider.CURSOR,
            providers={AIProvider.CURSOR: AnthropicConfig()},
        )

    message = str(excinfo.value)
    assert_that(message).contains("ai.providers.cursor")
    assert_that(message).contains("CursorConfig")
    assert_that(message).contains("AnthropicConfig")


def test_a_malformed_providers_value_is_rejected_not_overwritten() -> None:
    """A scalar ``ai.providers`` survives the legacy migration as an error.

    The migration used to replace any non-mapping ``providers`` value with an
    empty mapping before filling in the legacy key, so a malformed block was
    accepted rather than reported.
    """
    with pytest.raises(ValueError) as excinfo:
        AIConfig.model_validate(
            {
                "provider": "cursor",
                "providers": "cursor",
                "cursor_trust_workspace": False,
            },
        )

    assert_that(str(excinfo.value)).contains("ai.providers must be a mapping")


def test_a_malformed_inner_block_is_rejected_during_legacy_migration() -> None:
    """A scalar inner block is reported, not replaced, when a legacy key is set.

    Without a legacy key the same input is rejected by validation; the
    migration must not turn it into an empty block plus the migrated field.
    """
    with pytest.raises(ValueError) as excinfo:
        AIConfig.model_validate(
            {
                "provider": "cursor",
                "providers": {"cursor": 5},
                "cursor_trust_workspace": False,
            },
        )

    assert_that(str(excinfo.value)).contains(
        "ai.providers.cursor must be a mapping",
    )


def test_lintro_config_survives_rich_markup_in_an_override_value(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected override value containing markup does not break the report.

    The failure line quotes the value the user set, so ``[/]`` would close a
    tag the line never opened and Rich would raise ``MarkupError`` from the
    one command a user runs to diagnose a bad config.

    Args:
        isolated_project: Empty project directory with the user tier isolated.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(
        f"{ENV_PROVIDER_BLOCK_PREFIX}CURSOR__TRUST_WORKSPACE",
        "[/]",
    )
    (isolated_project / ".lintro-config.yaml").write_text(
        yaml.safe_dump({"ai": {"provider": "cursor"}}),
        encoding="utf-8",
    )
    clear_config_cache()

    result = CliRunner().invoke(cli, ["config"])

    clear_config_cache()
    assert_that(result.exit_code).is_equal_to(0)
    output = " ".join(result.output.split())
    assert_that(output).contains("[/]")
    assert_that(output).contains("Tool Execution Order")
