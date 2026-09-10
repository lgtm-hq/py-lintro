"""Tests for deps configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_loader import load_config
from lintro.config.deps_config import DepsPolicy
from lintro.exceptions.errors import ConfigurationError


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
    assert_that(config.deps.exceptions[0].allowed_types).is_equal_to(
        ["tilde", "caret"],
    )
    assert_that(config.deps.exceptions[0].reason).is_equal_to("test tooling")


def test_load_config_defaults_deps_when_absent(tmp_path: Path) -> None:
    """Config without a deps section yields default DepsConfig.

    Args:
        tmp_path: Temporary directory.
    """
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("execution:\n  parallel: true\n")
    config = load_config(config_path=config_file, allow_pyproject_fallback=False)
    assert_that(config.deps.policy).is_equal_to(DepsPolicy.FLEXIBLE)


def test_load_config_rejects_unknown_deps_key(tmp_path: Path) -> None:
    """A misspelled deps key fails loading instead of silently defaulting.

    Because ``deps`` gates dependency-spec enforcement, a typo such as
    ``pollicy`` must raise rather than fall back to the default policy and let
    non-conforming specs pass in CI.

    Args:
        tmp_path: Temporary directory.
    """
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("deps:\n  pollicy: strict\n")

    with pytest.raises(ValueError, match="Unknown deps config key"):
        load_config(config_path=config_file, allow_pyproject_fallback=False)


def test_load_config_rejects_non_mapping_deps_in_yaml(tmp_path: Path) -> None:
    """A scalar YAML ``deps`` section fails closed.

    Args:
        tmp_path: Temporary directory.
    """
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("deps: true\n")

    with pytest.raises(ConfigurationError, match="deps config must be a mapping"):
        load_config(config_path=config_file, allow_pyproject_fallback=False)


def test_load_config_rejects_non_mapping_deps_in_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scalar ``[tool.lintro] deps`` table fails closed.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.lintro]\ndeps = true\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigurationError, match="deps config must be a mapping"):
        load_config(allow_pyproject_fallback=True)
