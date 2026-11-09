"""Tests for ``execution.enabled_tools`` absent vs empty vs non-empty (#3066)."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_loader import _parse_execution_config, load_config
from lintro.config.config_validator import validate_config_file
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig
from lintro.exceptions.errors import ConfigurationError


def _write(tmp_path: Path, content: str) -> Path:
    """Write a Lintro YAML config into ``tmp_path``.

    Args:
        tmp_path: Pytest temporary directory.
        content: YAML document body.

    Returns:
        Path: The written config file.
    """
    path = tmp_path / ".lintro-config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loader_keeps_absent_key_as_none() -> None:
    """An absent key stays ``None`` so it is distinguishable from ``[]``."""
    assert_that(_parse_execution_config({}).enabled_tools).is_none()


def test_loader_passes_non_empty_list_through() -> None:
    """A non-empty list is preserved verbatim."""
    config = _parse_execution_config({"enabled_tools": ["ruff", "mypy"]})

    assert_that(config.enabled_tools).is_equal_to(["ruff", "mypy"])


def test_loader_rejects_explicit_empty_list() -> None:
    """An explicit ``[]`` is an error with an actionable message."""
    with pytest.raises(ValueError, match="omit the key to run all tools"):
        _parse_execution_config({"enabled_tools": []})


def test_load_config_empty_list_is_configuration_error(tmp_path: Path) -> None:
    """Loading a config with ``enabled_tools: []`` fails with ConfigurationError.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = _write(tmp_path, "execution:\n  enabled_tools: []\n")

    with pytest.raises(ConfigurationError, match="must not be an empty list"):
        load_config(config_path=str(path))


def test_validator_flags_empty_list_as_error(tmp_path: Path) -> None:
    """The validator reports ``enabled_tools: []`` as an error.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = _write(tmp_path, "execution:\n  enabled_tools: []\n")

    result = validate_config_file(path)

    assert_that(result.is_valid).is_false()
    rendered = [e.render() for e in result.errors]
    assert_that(any("omit the key to run all tools" in m for m in rendered)).is_true()


@pytest.mark.parametrize(
    "body",
    [
        "execution:\n  fail_fast: false\n",
        "execution:\n  enabled_tools: [ruff]\n",
    ],
    ids=["absent", "non-empty"],
)
def test_validator_accepts_absent_and_non_empty(tmp_path: Path, body: str) -> None:
    """Absent and non-empty ``enabled_tools`` both validate cleanly.

    Args:
        tmp_path: Pytest temporary directory.
        body: Config body under test.
    """
    result = validate_config_file(_write(tmp_path, body))

    assert_that(result.is_valid).is_true()


def test_none_allowlist_allows_every_tool() -> None:
    """``None`` means no allowlist: every tool is in it."""
    config = LintroConfig(execution=ExecutionConfig())

    assert_that(config.is_tool_in_enabled_tools("ruff")).is_true()


def test_non_empty_allowlist_is_strict_with_alias() -> None:
    """A non-empty list is membership-only and honours hyphen/underscore."""
    config = LintroConfig(
        execution=ExecutionConfig(enabled_tools=["ruff", "markdown_lint"]),
    )

    assert_that(config.is_tool_in_enabled_tools("ruff")).is_true()
    assert_that(config.is_tool_in_enabled_tools("markdown-lint")).is_true()
    assert_that(config.is_tool_in_enabled_tools("mypy")).is_false()
