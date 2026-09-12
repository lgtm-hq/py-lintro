"""Unit tests for BaseToolPlugin set_options and _setup_defaults methods."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.plugins.base import DEFAULT_EXCLUDE_PATTERNS

if TYPE_CHECKING:
    from tests.unit.plugins.conftest import FakeToolPlugin


# =============================================================================
# BaseToolPlugin.set_options Tests
# =============================================================================


@pytest.mark.parametrize(
    ("timeout_value", "expected"),
    [
        pytest.param(60, 60.0, id="integer_timeout"),
        pytest.param(45.5, 45.5, id="float_timeout"),
        pytest.param(0, 0.0, id="zero_timeout"),
    ],
)
def test_set_options_timeout_valid_values(
    fake_tool_plugin: FakeToolPlugin,
    timeout_value: int | float,
    expected: float,
) -> None:
    """Verify valid timeout values are accepted and stored correctly.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
        timeout_value: The timeout value to set.
        expected: The expected timeout value after setting.
    """
    fake_tool_plugin.set_options(timeout=timeout_value)

    assert_that(fake_tool_plugin.options.get("timeout")).is_equal_to(expected)


def test_set_options_timeout_none(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify timeout can be set to None.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    fake_tool_plugin.set_options(timeout=None)

    assert_that(fake_tool_plugin.options.get("timeout")).is_none()


def test_set_options_timeout_invalid_raises_value_error(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify invalid timeout type raises ValueError with descriptive message.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    with pytest.raises(ValueError, match="Timeout must be a number"):
        fake_tool_plugin.set_options(timeout="invalid")


def test_set_options_exclude_patterns_merges_with_existing(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify CLI exclude patterns are merged with existing defaults.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    original_patterns = list(fake_tool_plugin.exclude_patterns)
    cli_patterns = ["*.log", "*.tmp"]
    fake_tool_plugin.set_options(exclude_patterns=cli_patterns)

    # CLI patterns are added
    for p in cli_patterns:
        assert_that(fake_tool_plugin.exclude_patterns).contains(p)

    # Existing default patterns are preserved
    for p in original_patterns:
        assert_that(fake_tool_plugin.exclude_patterns).contains(p)


def test_set_options_exclude_patterns_does_not_duplicate(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify duplicate patterns are not added when merging.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    pattern = DEFAULT_EXCLUDE_PATTERNS[0]
    original_count = len(fake_tool_plugin.exclude_patterns)
    fake_tool_plugin.set_options(exclude_patterns=[pattern])

    assert_that(fake_tool_plugin.exclude_patterns).is_length(original_count)


def test_set_options_exclude_patterns_preserves_lintro_ignore(
    tmp_path: Path,
) -> None:
    """Verify CLI --exclude merges with .lintro-ignore instead of replacing.

    This is the exact scenario from issue #580: .lintro-ignore lists
    test_samples/ but CLI --exclude passes a different set of patterns.
    Both must be present in the final exclude list.

    Args:
        tmp_path: Temporary directory path for testing.
    """
    from tests.unit.plugins.conftest import FakeToolPlugin

    ignore_file = tmp_path / ".lintro-ignore"
    ignore_file.write_text("test_samples/\ncustom_dir\n")

    with patch(
        "lintro.utils.path_filtering.find_lintro_ignore",
        return_value=ignore_file,
    ):
        plugin = FakeToolPlugin()

    # Simulate CLI --exclude (same flow as tool_configuration.py)
    cli_excludes = [".pytest_cache", ".mypy_cache", "htmlcov"]
    plugin.set_options(exclude_patterns=cli_excludes)

    # CLI patterns are present
    for p in cli_excludes:
        assert_that(plugin.exclude_patterns).contains(p)

    # .lintro-ignore patterns are preserved
    assert_that(plugin.exclude_patterns).contains("test_samples/")
    assert_that(plugin.exclude_patterns).contains("custom_dir")

    # Default patterns are preserved
    for p in DEFAULT_EXCLUDE_PATTERNS:
        assert_that(plugin.exclude_patterns).contains(p)


def test_set_options_exclude_patterns_invalid_raises_value_error(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify non-list exclude patterns raises ValueError.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    with pytest.raises(ValueError, match="Exclude patterns must be a list"):
        fake_tool_plugin.set_options(exclude_patterns="*.log")


def test_set_options_include_venv_valid(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify valid boolean include_venv is accepted.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    fake_tool_plugin.set_options(include_venv=True)

    assert_that(fake_tool_plugin.include_venv).is_true()


def test_set_options_include_venv_invalid_raises_value_error(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify non-boolean include_venv raises ValueError.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    with pytest.raises(ValueError, match="Include venv must be a boolean"):
        fake_tool_plugin.set_options(include_venv="yes")


# =============================================================================
# BaseToolPlugin._setup_defaults Tests
# =============================================================================


def test_setup_defaults_adds_default_exclude_patterns(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify default exclude patterns are added to plugin's exclude_patterns.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    for pattern in DEFAULT_EXCLUDE_PATTERNS:
        assert_that(pattern in fake_tool_plugin.exclude_patterns).is_true()

    assert_that(fake_tool_plugin.exclude_patterns).is_not_empty()


def test_setup_defaults_adds_lintro_ignore_patterns(tmp_path: Path) -> None:
    """Verify patterns from .lintro-ignore file are added to exclude_patterns.

    Args:
        tmp_path: Temporary directory path for testing.
    """
    from tests.unit.plugins.conftest import FakeToolPlugin

    ignore_file = tmp_path / ".lintro-ignore"
    ignore_file.write_text("custom_pattern\n# comment\n\nother_pattern\n")

    with patch(
        "lintro.utils.path_filtering.find_lintro_ignore",
        return_value=ignore_file,
    ):
        plugin = FakeToolPlugin()

        assert_that("custom_pattern" in plugin.exclude_patterns).is_true()
        assert_that("other_pattern" in plugin.exclude_patterns).is_true()


def test_setup_defaults_handles_lintro_ignore_read_error_gracefully() -> None:
    """Verify .lintro-ignore read errors are handled without raising."""
    from tests.unit.plugins.conftest import FakeToolPlugin

    with patch(
        "lintro.utils.path_filtering.find_lintro_ignore",
        side_effect=PermissionError("Access denied"),
    ):
        plugin = FakeToolPlugin()

        # Should not raise, just log debug
        assert_that(plugin.exclude_patterns).is_not_empty()


def test_setup_defaults_sets_default_timeout_from_definition(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify default timeout is set from tool definition.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    assert_that(fake_tool_plugin.options.get("timeout")).is_equal_to(30)
