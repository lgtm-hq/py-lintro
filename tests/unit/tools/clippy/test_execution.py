"""Unit tests for clippy plugin check execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.clippy import ClippyPlugin

# Tests for ClippyPlugin.check method


def test_check_without_cargo_toml_skips(
    clippy_plugin: ClippyPlugin,
    tmp_path: Path,
) -> None:
    """Check skips cleanly when no Cargo.toml is present.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    rs_file = tmp_path / "main.rs"
    rs_file.write_text("fn main() {}\n")

    result = clippy_plugin.check([str(rs_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No Cargo.toml found")


def test_check_with_clean_run(clippy_plugin: ClippyPlugin, tmp_path: Path) -> None:
    """Check reports success when clippy finds no issues.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n')
    rs_file = tmp_path / "main.rs"
    rs_file.write_text("fn main() {}\n")

    with patch.object(clippy_plugin, "_run_subprocess", return_value=(True, "")):
        result = clippy_plugin.check([str(rs_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
