"""Delete-only ratchet for mypy's None-related checks (#3022, epic #2996)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NONE_CODES = frozenset(
    {
        "comparison-overlap",
        "unreachable",
        "redundant-expr",
        "truthy-bool",
        "possibly-undefined",
    },
)

# Freeze module/code identities as well as counts. Delete entries here and in
# pyproject.toml together as their owning refactors land; never add entries.
BASELINE: dict[str, tuple[str, ...]] = {
    "lintro._tool_versions": ("redundant-expr",),
    "lintro.ai.config_overrides": ("unreachable",),
    "lintro.ai.providers.base": ("unreachable",),
    "lintro.ai.rate_limit": ("comparison-overlap", "unreachable"),
    "lintro.ai.review.exceptions": ("unreachable",),
    "lintro.ai.review.models.changed_file": ("unreachable",),
    "lintro.ai.transport": ("comparison-overlap", "redundant-expr"),
    "lintro.config.config_loader": ("comparison-overlap", "redundant-expr"),
    "lintro.config.tool_config_generator": ("comparison-overlap",),
    "lintro.parsers.bandit.bandit_parser": ("unreachable",),
    "lintro.parsers.semgrep.semgrep_parser": (
        "comparison-overlap",
        "redundant-expr",
        "unreachable",
    ),
    "lintro.parsers.streaming": ("redundant-expr",),
    "lintro.plugins.discovery": ("unreachable",),
    "lintro.plugins.subprocess_executor": ("redundant-expr", "unreachable"),
    "lintro.tools.core.tool_registry": ("unreachable",),
    "lintro.tools.pytest.collection": ("unreachable",),
    "lintro.tools.pytest.coverage_processor": ("comparison-overlap",),
    "lintro.tools.pytest.definition": ("comparison-overlap", "redundant-expr"),
    "lintro.tools.pytest.output_parsers": ("unreachable",),
    "lintro.tools.pytest.test_analytics": ("redundant-expr",),
    "lintro.utils.async_tool_executor": ("redundant-expr", "unreachable"),
    "lintro.utils.config_priority": ("redundant-expr",),
    "lintro.utils.config_validation": ("redundant-expr",),
    "lintro.utils.environment.collectors": ("possibly-undefined",),
    "lintro.utils.execution.fix_retry": ("redundant-expr", "unreachable"),
    "lintro.utils.execution.result_shaping": ("comparison-overlap", "unreachable"),
    "lintro.utils.file_cache": ("possibly-undefined",),
    "lintro.utils.json_output": ("redundant-expr",),
    "lintro.utils.native_parsers": (
        "comparison-overlap",
        "possibly-undefined",
        "unreachable",
    ),
}
BASELINE_MAX_MODULES = 29
BASELINE_MAX_SUPPRESSIONS = 41


def _mypy_config() -> dict[str, Any]:
    """Load the repository's mypy policy.

    Returns:
        The parsed mypy configuration.
    """
    parsed = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return dict(parsed["tool"]["mypy"])


def test_none_checks_stay_enabled() -> None:
    """Strict mode alone does not enable these additional checks."""
    config = _mypy_config()

    assert_that(config.get("strict")).is_true()
    assert_that(config.get("strict_equality", True)).is_true()
    assert_that(config.get("strict_equality_for_none")).is_true()
    assert_that(config.get("warn_unreachable")).is_true()
    assert_that(config.get("enable_error_code", [])).contains(
        "redundant-expr",
        "truthy-bool",
        "possibly-undefined",
    )
    assert_that(
        _NONE_CODES.intersection(config.get("disable_error_code", [])),
    ).is_empty()
    assert_that(config.get("ignore_errors", False)).is_false()


def test_overrides_cannot_bypass_none_checks() -> None:
    """Overrides must use the specific codes counted by the ratchet."""
    bypasses = [
        override
        for override in _mypy_config().get("overrides", [])
        if override.get("ignore_errors", False)
        or any(
            override.get(setting) is False
            for setting in (
                "strict_equality",
                "strict_equality_for_none",
                "warn_unreachable",
            )
        )
    ]

    assert_that(bypasses).described_as(
        "disable only recorded error codes, never a whole check or module",
    ).is_empty()


def test_none_baseline_matches_config_exactly() -> None:
    """Only recorded module/code pairs may disable the new diagnostics."""
    configured = {
        (module, code)
        for override in _mypy_config().get("overrides", [])
        for module in (
            [override["module"]]
            if isinstance(override["module"], str)
            else override["module"]
        )
        for code in override.get("disable_error_code", [])
        if code in _NONE_CODES
    }
    frozen = {(module, code) for module, codes in BASELINE.items() for code in codes}

    assert_that(sorted(configured - frozen)).described_as(
        "None-check overrides may only shrink; never add a module or code",
    ).is_empty()
    assert_that(sorted(frozen - configured)).described_as(
        "prune BASELINE with pyproject.toml so retired overrides cannot return",
    ).is_empty()


def test_none_baseline_may_only_shrink() -> None:
    """Lower both ceilings with the baseline so no slack permits regrowth."""
    assert_that(len(BASELINE)).is_equal_to(BASELINE_MAX_MODULES)
    assert_that(sum(len(codes) for codes in BASELINE.values())).is_equal_to(
        BASELINE_MAX_SUPPRESSIONS,
    )
    missing = [
        module
        for module in BASELINE
        if not (_REPO_ROOT / (module.replace(".", "/") + ".py")).is_file()
    ]
    assert_that(missing).described_as("delete overrides for removed modules").is_empty()
