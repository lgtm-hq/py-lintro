"""A real golangci-lint ``--fix`` that could not write its fix (#3352)."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("golangci-lint")

_CONFIG = """version: "2"
linters:
  default: none
  enable:
    - godot
    - errcheck
"""

_SOURCE = """package main

import "os"

// Main removes a file
func main() {
\tos.Remove("x")
}
"""


@pytest.fixture
def read_only_module(tmp_path: Path) -> Iterator[Path]:
    """Provide a Go module with a fixable finding in a read-only file.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Yields:
        Path: The module directory, writable again after the test.
    """
    (tmp_path / "go.mod").write_text("module example.com/fixme\n\ngo 1.22\n")
    (tmp_path / ".golangci.yml").write_text(_CONFIG)
    source = tmp_path / "main.go"
    source.write_text(_SOURCE)
    source.chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        if os.access(source, os.W_OK):
            pytest.skip("file permissions do not apply to this user (root)")
        yield tmp_path
    finally:
        tmp_path.chmod(0o755)
        source.chmod(0o644)


def test_fix_that_cannot_write_is_an_incomplete_fix(
    get_plugin: Callable[[str], BaseToolPlugin],
    read_only_module: Path,
) -> None:
    """golangci-lint exits 1 with a valid report; its fixer error is kept.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        read_only_module: Module whose source cannot be rewritten.
    """
    result = get_plugin("golangci_lint").fix([str(read_only_module)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.output).contains("Failed to fix issues").contains(
        "permission denied",
    )
    assert_that((read_only_module / "main.go").read_text()).contains(
        "// Main removes a file\n",
    )
