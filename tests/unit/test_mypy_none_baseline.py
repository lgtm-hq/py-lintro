"""Delete-only ratchet for mypy's None-related checks (#3022, epic #2996)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
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
    "lintro.ai.rate_limit": ("unreachable",),
    "lintro.ai.review.exceptions": ("unreachable",),
    "lintro.ai.review.models.changed_file": ("unreachable",),
    "lintro.ai.transport": ("redundant-expr",),
    "lintro.config.config_loader": ("redundant-expr",),
    "lintro.parsers.bandit.bandit_parser": ("unreachable",),
    "lintro.parsers.semgrep.semgrep_parser": (
        "redundant-expr",
        "unreachable",
    ),
    "lintro.parsers.streaming": ("redundant-expr",),
    "lintro.plugins.discovery": ("unreachable",),
    "lintro.plugins.subprocess_executor": ("redundant-expr", "unreachable"),
    "lintro.tools.core.tool_registry": ("unreachable",),
    "lintro.tools.pytest.collection": ("unreachable",),
    "lintro.tools.pytest.definition": ("redundant-expr",),
    "lintro.tools.pytest.output_parsers": ("unreachable",),
    "lintro.tools.pytest.test_analytics": ("redundant-expr",),
    "lintro.utils.async_tool_executor": ("redundant-expr", "unreachable"),
    "lintro.utils.config_priority": ("redundant-expr",),
    "lintro.utils.config_validation": ("redundant-expr",),
    "lintro.utils.environment.collectors": ("possibly-undefined",),
    "lintro.utils.execution.fix_retry": ("redundant-expr", "unreachable"),
    "lintro.utils.execution.result_shaping": ("unreachable",),
    "lintro.utils.file_cache": ("possibly-undefined",),
    "lintro.utils.json_output": ("redundant-expr",),
    "lintro.utils.native_parsers": (
        "possibly-undefined",
        "unreachable",
    ),
}
# These nine modules previously disabled comparison-overlap. Only their new
# None-specific check may be relaxed; strict equality must still run.
NONE_EQUALITY_BASELINE = frozenset(
    {
        "lintro.ai.rate_limit",
        "lintro.ai.transport",
        "lintro.config.config_loader",
        "lintro.config.tool_config_generator",
        "lintro.parsers.semgrep.semgrep_parser",
        "lintro.tools.pytest.coverage_processor",
        "lintro.tools.pytest.definition",
        "lintro.utils.execution.result_shaping",
        "lintro.utils.native_parsers",
    },
)
NONE_EQUALITY_MAX_MODULES = 9
BASELINE_MAX_MODULES = 29
BASELINE_MAX_SUPPRESSIONS = 41


def _mypy_config() -> dict[str, Any]:
    """Load the repository's mypy policy.

    Returns:
        The parsed mypy configuration.
    """
    parsed = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return dict(parsed["tool"]["mypy"])


def _error_codes(config: dict[str, Any], setting: str) -> set[str]:
    """Read a list-valued mypy error-code setting without silently losing it.

    Args:
        config: Global or per-module mypy options.
        setting: The enable or disable error-code option.

    Returns:
        The configured codes after validating their representation.
    """
    codes = config.get(setting, [])
    assert_that(codes).is_instance_of(list)
    assert_that(all(isinstance(code, str) for code in codes)).is_true()
    return set(codes)


def _assert_enabled(config: dict[str, Any]) -> None:
    """Assert the global checks remain enabled.

    Args:
        config: Global mypy options.
    """
    assert_that(config.get("strict")).is_true()
    assert_that(config.get("strict_optional", True)).is_true()
    assert_that(config.get("strict_equality", True)).is_true()
    assert_that(config.get("strict_equality_for_none")).is_true()
    assert_that(config.get("warn_unreachable")).is_true()
    assert_that(_error_codes(config, "enable_error_code")).contains(
        "redundant-expr",
        "truthy-bool",
        "possibly-undefined",
    )
    assert_that(
        _NONE_CODES.intersection(_error_codes(config, "disable_error_code")),
    ).is_empty()
    assert_that(config.get("ignore_errors", False)).is_false()


def _configured_baseline(
    config: dict[str, Any],
) -> tuple[set[tuple[str, str]], set[str]]:
    """Validate overrides and collect both kinds of exact-module exceptions.

    Args:
        config: Global mypy options containing the overrides.

    Returns:
        Disabled module/code pairs and modules relaxing only None equality.
    """
    configured: set[tuple[str, str]] = set()
    none_equality: set[str] = set()
    for override in config.get("overrides", []):
        assert_that(override.get("ignore_errors", False)).is_false()
        for setting in (
            "strict",
            "strict_optional",
            "strict_equality",
            "warn_unreachable",
        ):
            assert_that(override.get(setting, True)).is_true()
        if "enable_error_code" in override:
            assert_that(_error_codes(override, "enable_error_code")).contains(
                "redundant-expr",
                "truthy-bool",
                "possibly-undefined",
            )
        codes = _error_codes(override, "disable_error_code")
        assert_that(codes).does_not_contain("comparison-overlap")
        modules = override["module"]
        if isinstance(modules, str):
            modules = [modules]
        assert_that(modules).is_instance_of(list)
        assert_that(all(isinstance(module, str) for module in modules)).is_true()
        configured.update(
            (module, code) for module in modules for code in codes & _NONE_CODES
        )
        if override.get("strict_equality_for_none") is False:
            none_equality.update(modules)
    return configured, none_equality


def _assert_baseline(config: dict[str, Any]) -> None:
    """Require exact identities so retired or wildcard exceptions cannot return.

    Args:
        config: Global mypy options containing the overrides.
    """
    configured, none_equality = _configured_baseline(config)
    frozen = {(module, code) for module, codes in BASELINE.items() for code in codes}
    assert_that(sorted(configured)).is_equal_to(sorted(frozen))
    assert_that(sorted(none_equality)).is_equal_to(sorted(NONE_EQUALITY_BASELINE))


def test_none_checks_stay_enabled() -> None:
    """Strict mode alone does not enable these additional checks."""
    _assert_enabled(_mypy_config())


def test_none_baseline_matches_config_exactly() -> None:
    """Only recorded module/code pairs and None-specific settings may relax checks."""
    _assert_baseline(_mypy_config())


def test_none_baseline_may_only_shrink() -> None:
    """Lower ceilings with each retirement so no slack permits regrowth."""
    modules = BASELINE.keys() | NONE_EQUALITY_BASELINE
    assert_that(len(modules)).is_equal_to(BASELINE_MAX_MODULES)
    assert_that(len(NONE_EQUALITY_BASELINE)).is_equal_to(NONE_EQUALITY_MAX_MODULES)
    assert_that(
        sum(len(codes) for codes in BASELINE.values()) + len(NONE_EQUALITY_BASELINE),
    ).is_equal_to(BASELINE_MAX_SUPPRESSIONS)
    missing = [
        module
        for module in modules
        if not any(
            (_REPO_ROOT / (module.replace(".", "/") + suffix)).is_file()
            for suffix in (".py", "/__init__.py")
        )
    ]
    assert_that(missing).described_as("delete overrides for removed modules").is_empty()


@pytest.mark.parametrize("setting", ["enable_error_code", "disable_error_code"])
def test_guard_rejects_string_valued_codes(setting: str) -> None:
    """A bare string must fail instead of being iterated character by character.

    Args:
        setting: Error-code setting to corrupt.
    """
    config = _mypy_config()
    config[setting] = "unreachable"
    with pytest.raises(AssertionError):
        _assert_enabled(config)


@pytest.mark.parametrize(
    "override",
    [
        {"ignore_errors": True},
        {"strict": False},
        {"strict_optional": False},
        {"strict_equality": False},
        {"warn_unreachable": False},
        {"enable_error_code": []},
        {"disable_error_code": "unreachable"},
        {"disable_error_code": ["comparison-overlap"]},
        {"module": ["lintro.*"], "disable_error_code": ["unreachable"]},
        {"module": ["lintro.*"], "strict_equality_for_none": False},
        {"module": ["lintro.new_module"], "strict_equality_for_none": False},
    ],
)
def test_guard_rejects_override_bypasses(override: dict[str, Any]) -> None:
    """Unrecorded suppressions and settings cannot bypass the exact-module ratchet.

    Args:
        override: Invalid override to append to the real configuration.
    """
    config = _mypy_config()
    config["overrides"].append({"module": ["lintro.ai.rate_limit"], **override})
    with pytest.raises(AssertionError):
        _assert_baseline(config)


def test_none_equality_retirement_requires_baseline_pruning() -> None:
    """A removed None exception must be pruned so it cannot silently return."""
    config = _mypy_config()
    for override in config["overrides"]:
        if override["module"] == ["lintro.ai.rate_limit"]:
            override.pop("strict_equality_for_none")
    with pytest.raises(AssertionError):
        _assert_baseline(config)
