"""Tests for deps configuration loading."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.config.config_loader import load_config
from lintro.config.deps_config import DepsPolicy


def test_load_config_parses_deps_section(tmp_path: Path) -> None:
    """A deps section in config is parsed into DepsConfig.

    Args:
        tmp_path: Temporary directory.
    """
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "deps:",
                "  policy: strict",
                "  exceptions:",
                '    - package: "pytest"',
                "      allowed_types: [tilde, caret]",
                '      reason: "test tooling"',
            ],
        ),
    )
    config = load_config(config_path=config_file, allow_pyproject_fallback=False)
    assert_that(config.deps.policy).is_equal_to(DepsPolicy.STRICT)
    assert_that(config.deps.exceptions).is_length(1)
    assert_that(config.deps.exceptions[0].package).is_equal_to("pytest")


def test_load_config_defaults_deps_when_absent(tmp_path: Path) -> None:
    """Config without a deps section yields default DepsConfig.

    Args:
        tmp_path: Temporary directory.
    """
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("execution:\n  parallel: true\n")
    config = load_config(config_path=config_file, allow_pyproject_fallback=False)
    assert_that(config.deps.policy).is_equal_to(DepsPolicy.FLEXIBLE)
