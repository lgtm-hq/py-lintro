"""Unit tests for yamllint plugin check execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.yamllint import YamllintPlugin

# Tests for YamllintPlugin.check method


def test_check_clean_file(yamllint_plugin: YamllintPlugin, tmp_path: Path) -> None:
    """Check reports success when no issues found.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("key: value\n")

    with patch.object(yamllint_plugin, "_run_subprocess", return_value=(True, "")):
        result = yamllint_plugin.check([str(yaml_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_issues(yamllint_plugin: YamllintPlugin, tmp_path: Path) -> None:
    """Check parses issues reported by yamllint.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("key:   value\n")

    output = f"{yaml_file}:1:6: [warning] too many spaces after colon (colons)"

    with patch.object(
        yamllint_plugin,
        "_run_subprocess",
        return_value=(False, output),
    ):
        result = yamllint_plugin.check([str(yaml_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_check_no_yaml_files(yamllint_plugin: YamllintPlugin, tmp_path: Path) -> None:
    """Check returns success when no YAML files found.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_yaml_file = tmp_path / "notes.txt"
    non_yaml_file.write_text("hello")

    result = yamllint_plugin.check([str(non_yaml_file)], {})

    assert_that(result.success).is_true()


def test_fix_raises_not_implemented(yamllint_plugin: YamllintPlugin) -> None:
    """Fix raises NotImplementedError.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="Yamllint cannot automatically"):
        yamllint_plugin.fix([], {})
