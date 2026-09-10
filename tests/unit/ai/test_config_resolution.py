"""Tests for decoupling ``AIConfig`` parsing from the core config loader.

Issue #724 PR 3 removed the last two ``lintro.config`` -> ``lintro.ai`` import
edges: ``LintroConfig.ai`` now stores the ``ai:`` section verbatim as a raw
mapping and the AI layer parses it via
:func:`lintro.ai.effective_config.resolve_effective_ai_config`, exposed on the
facade as :func:`lintro.ai.interface.resolve_ai_config`.

The behavioural risk this creates is warning *timing*: unknown ``ai.*`` keys
used to be reported at config-load time. These tests pin that a typo is still
reported exactly once on the paths a user actually runs.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.config import AIConfig
from lintro.ai.effective_config import resolve_effective_ai_config
from lintro.ai.enums import AITransport, ConfidenceLevel
from lintro.ai.interface import resolve_ai_config, run_ai_layer
from lintro.ai.registry import AIProvider
from lintro.config.config_loader import load_config
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action


@pytest.fixture
def warnings_captured() -> Iterator[list[str]]:
    """Capture loguru WARNING records emitted during the test.

    Yields:
        list[str]: A list that accumulates formatted warning messages.
    """
    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="WARNING",
    )
    try:
        yield messages
    finally:
        logger.remove(handler_id)


# ---------------------------------------------------------------------------
# resolve_effective_ai_config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("data", [None, {}])
def test_resolve_effective_ai_config_empty_yields_model_defaults(
    data: dict[str, Any] | None,
) -> None:
    """An absent or empty ``ai:`` section produces a default AIConfig.

    Args:
        data: The raw mapping under test.
    """
    assert_that(resolve_effective_ai_config(data).config).is_equal_to(AIConfig())


def test_resolve_effective_ai_config_applies_known_keys() -> None:
    """Recognized keys are applied and omitted ones keep model defaults."""
    config = resolve_effective_ai_config(
        {"enabled": True, "lint": True, "max_tokens": 1234},
    ).config

    assert_that(config.enabled).is_true()
    assert_that(config.lint).is_true()
    assert_that(config.review).is_false()
    assert_that(config.max_tokens).is_equal_to(1234)
    assert_that(config.provider).is_none()


def test_resolve_effective_ai_config_drops_unknown_keys_and_warns_sorted(
    warnings_captured: list[str],
) -> None:
    """Unknown keys are dropped, not rejected, and listed sorted in a warning.

    Args:
        warnings_captured: Captured loguru warning messages.
    """
    config = resolve_effective_ai_config(
        {"enabled": True, "zulu_typo": 1, "alpha_typo": 2},
    ).config

    assert_that(config.enabled).is_true()
    assert_that(config.model_dump()).does_not_contain_key("zulu_typo")
    assert_that("".join(warnings_captured)).contains(
        "Unknown AI config keys ignored: alpha_typo, zulu_typo",
    )


def test_resolve_effective_ai_config_can_suppress_the_unknown_key_warning(
    warnings_captured: list[str],
) -> None:
    """Display callers opt out of diagnostics without changing parsing.

    Args:
        warnings_captured: Captured loguru warning messages.
    """
    config = resolve_effective_ai_config(
        {"lint": True, "typo": 1},
        diagnostics=False,
    ).config

    assert_that(config.lint).is_true()
    assert_that(warnings_captured).is_empty()


def test_resolve_effective_ai_config_warns_on_legacy_enabled_only_config(
    warnings_captured: list[str],
) -> None:
    """A legacy ``enabled``-only mapping still gets the migration hint.

    Args:
        warnings_captured: Captured loguru warning messages.
    """
    config = resolve_effective_ai_config({"enabled": True}).config

    assert_that(config.lint).is_true()
    assert_that(config.review).is_true()
    assert_that("".join(warnings_captured)).contains(
        "ai.enabled without ai.lint/ai.review is deprecated",
    )


def test_resolve_effective_ai_config_suppresses_the_legacy_deprecation_hint(
    warnings_captured: list[str],
) -> None:
    """Display-only parsing does not repeat the legacy migration hint.

    Regression guard: the resolver reports it once per run, so the
    pre-execution status renderer must not emit a second copy.

    Args:
        warnings_captured: Captured loguru warning messages.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        config = resolve_effective_ai_config(
            {"enabled": True},
            diagnostics=False,
        ).config

    assert_that(config.lint).is_true()
    assert_that(config.review).is_true()
    assert_that(warnings_captured).is_empty()


# ---------------------------------------------------------------------------
# Loader round trip
# ---------------------------------------------------------------------------


def test_loader_stores_the_ai_section_verbatim(tmp_path: Path) -> None:
    """The loader keeps the raw mapping and never builds an AIConfig.

    Args:
        tmp_path: Temporary directory for the config file.
    """
    (tmp_path / ".lintro-config.yaml").write_text(
        "ai:\n  enabled: true\n  lint: true\n  bogus_key: 1\n",
        encoding="utf-8",
    )

    config = load_config(
        config_path=tmp_path / ".lintro-config.yaml",
        allow_pyproject_fallback=False,
    )

    assert_that(config.ai).is_instance_of(dict)
    assert_that(config.ai).is_equal_to(
        {"enabled": True, "lint": True, "bogus_key": 1},
    )


def test_loader_missing_ai_section_yields_empty_mapping(tmp_path: Path) -> None:
    """A config without an ``ai:`` section resolves to a default AIConfig.

    Args:
        tmp_path: Temporary directory for the config file.
    """
    (tmp_path / ".lintro-config.yaml").write_text(
        "execution:\n  fail_fast: true\n",
        encoding="utf-8",
    )

    config = load_config(
        config_path=tmp_path / ".lintro-config.yaml",
        allow_pyproject_fallback=False,
    )

    assert_that(config.ai).is_equal_to({})
    assert_that(resolve_ai_config(config)).is_equal_to(AIConfig())


def test_full_ai_section_round_trips_to_the_same_config(tmp_path: Path) -> None:
    """A realistic ``ai:`` section parses exactly as it did before the split.

    Args:
        tmp_path: Temporary directory for the config file.
    """
    (tmp_path / ".lintro-config.yaml").write_text(
        "\n".join(
            [
                "ai:",
                "  enabled: true",
                "  lint: true",
                "  review: false",
                "  provider: openai",
                "  transport: cli",
                "  model: gpt-4-turbo",
                "  api_key_env: MY_API_KEY",
                "  max_tokens: 2048",
                "  max_parallel_calls: 3",
                "  fail_on_unfixed: true",
                "  fail_on_ai_error: true",
                "  min_confidence: high",
                "  exclude_paths:",
                "    - tests/*",
                "",
            ],
        ),
        encoding="utf-8",
    )

    ai_config = resolve_ai_config(
        load_config(
            config_path=tmp_path / ".lintro-config.yaml",
            allow_pyproject_fallback=False,
        ),
    )

    assert_that(ai_config.enabled).is_true()
    assert_that(ai_config.lint).is_true()
    assert_that(ai_config.review).is_false()
    assert_that(ai_config.lint_enabled).is_true()
    assert_that(ai_config.review_enabled).is_false()
    assert_that(ai_config.provider).is_equal_to(AIProvider.OPENAI)
    assert_that(ai_config.transport).is_equal_to(AITransport.CLI)
    assert_that(ai_config.model).is_equal_to("gpt-4-turbo")
    assert_that(ai_config.api_key_env).is_equal_to("MY_API_KEY")
    assert_that(ai_config.max_tokens).is_equal_to(2048)
    assert_that(ai_config.max_parallel_calls).is_equal_to(3)
    assert_that(ai_config.fail_on_unfixed).is_true()
    assert_that(ai_config.fail_on_ai_error).is_true()
    assert_that(ai_config.min_confidence).is_equal_to(ConfidenceLevel.HIGH)
    assert_that(ai_config.exclude_paths).is_equal_to(["tests/*"])
    # Untouched fields still fall back to the model defaults.
    assert_that(ai_config.api_timeout).is_equal_to(AIConfig().api_timeout)


def test_resolve_ai_config_reads_the_raw_mapping() -> None:
    """The facade turns the stored mapping into a typed configuration."""
    lintro_config = LintroConfig(ai={"enabled": True, "max_fix_attempts": 7})

    ai_config = resolve_ai_config(lintro_config)

    assert_that(ai_config).is_instance_of(AIConfig)
    assert_that(ai_config.enabled).is_true()
    assert_that(ai_config.max_fix_attempts).is_equal_to(7)


# ---------------------------------------------------------------------------
# Unknown-key discoverability (the behaviour change this PR makes)
# ---------------------------------------------------------------------------


def test_typo_is_reported_once_on_a_check_run_with_ai_disabled(
    warnings_captured: list[str],
) -> None:
    """A typo'd key still reaches the user on ``lintro chk`` with AI off.

    ``run_ai_layer`` is injected unconditionally by the check/format handlers
    and resolves the AI config before the ``should_run`` gate, so the warning
    fires whether or not AI is enabled -- and exactly once, because the
    resolved config is threaded down instead of re-parsed.

    Args:
        warnings_captured: Captured loguru warning messages.
    """
    lintro_config = LintroConfig(ai={"enabled": False, "revieww": True})

    outcome = run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=lintro_config,
        console_logger=MagicMock(),
        output_format="grid",
    )

    assert_that(outcome.ran).is_false()
    unknown_key_warnings = [
        message for message in warnings_captured if "Unknown AI config keys" in message
    ]
    assert_that(unknown_key_warnings).is_length(1)
    assert_that(unknown_key_warnings[0]).contains("revieww")


def test_typo_is_reported_on_a_json_run_where_the_summary_is_suppressed(
    warnings_captured: list[str],
) -> None:
    """Machine-readable output does not hide the typo warning.

    The pre-execution summary (the other resolver on the run path) is
    suppressed for json/sarif, so this pins that the AI entry point is what
    keeps the warning reachable.

    Args:
        warnings_captured: Captured loguru warning messages.
    """
    run_ai_layer(
        action=Action.CHECK,
        all_results=[],
        lintro_config=LintroConfig(ai={"typo_key": True}),
        console_logger=MagicMock(),
        output_format="json",
    )

    assert_that("".join(warnings_captured)).contains("typo_key")


def test_typo_is_reported_by_doctor(warnings_captured: list[str]) -> None:
    """``lintro doctor`` resolves the AI config, so it reports typos too.

    Args:
        warnings_captured: Captured loguru warning messages.
    """
    from lintro.ai.doctor_checks import check_ai_configuration

    lintro_config = LintroConfig(ai={"enabled": True, "provdier": "anthropic"})

    checks = check_ai_configuration(resolve_ai_config(lintro_config))

    assert_that(checks).is_not_empty()
    assert_that("".join(warnings_captured)).contains(
        "Unknown AI config keys ignored: provdier",
    )
