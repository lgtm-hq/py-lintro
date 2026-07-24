"""Regression tests for the silent-pass hole reported in issue #1678.

Two outcomes must stay distinguishable:

* **cannot run** — the tool's binary could not be resolved. The result must be
  a loud skip carrying a reason, never a plain ``PASS``.
* **nothing to check** — the tool ran its discovery and no file matched its
  patterns. That is a legitimate pass and must not regress into a skip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.plugins import execution_preparation
from lintro.tools.definitions.oxlint import OxlintPlugin
from lintro.tools.definitions.prettier import PrettierPlugin

if TYPE_CHECKING:
    from pathlib import Path

_MISSING_BINARY: str = "lintro-nonexistent-binary-1678"

_BADLY_FORMATTED_MARKDOWN: str = (
    "# probe\n\n- a deliberately badly wrapped markdown line far longer than "
    "eighty eight characters so prettier proseWrap always must reflow it\n"
)


@pytest.fixture
def unresolvable_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every tool binary unresolvable for the duration of a test.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        execution_preparation,
        "get_executable_command",
        lambda tool_name: [_MISSING_BINARY],
    )


def test_prettier_cannot_run_is_a_loud_skip_not_a_pass(
    tmp_path: Path,
    unresolvable_binary: None,
) -> None:
    """Prettier that cannot resolve its binary reports a skip with a reason.

    Args:
        tmp_path: Temporary directory fixture.
        unresolvable_binary: Fixture making the binary unresolvable.
    """
    target = tmp_path / "probe.md"
    target.write_text(_BADLY_FORMATTED_MARKDOWN, encoding="utf-8")

    plugin = PrettierPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(target)], {})

    assert_that(result.skipped).is_true()
    assert_that(result.skip_reason).is_not_none()
    assert_that(result.skip_reason).is_not_equal_to("")
    assert_that(result.output).contains("Skipping prettier")


def test_oxlint_cannot_run_is_a_loud_skip_not_a_pass(
    tmp_path: Path,
    unresolvable_binary: None,
) -> None:
    """A sibling Node-backed tool shares the same cannot-run contract.

    Args:
        tmp_path: Temporary directory fixture.
        unresolvable_binary: Fixture making the binary unresolvable.
    """
    target = tmp_path / "probe.js"
    target.write_text("const x = 1\n", encoding="utf-8")

    plugin = OxlintPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(target)], {})

    assert_that(result.skipped).is_true()
    assert_that(result.skip_reason).is_not_none()
    assert_that(result.skip_reason).is_not_equal_to("")


def test_prettier_with_no_matching_files_is_a_plain_pass(tmp_path: Path) -> None:
    """No file matching prettier's patterns stays a legitimate pass.

    Args:
        tmp_path: Temporary directory fixture.
    """
    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")

    plugin = PrettierPlugin()
    plugin.exclude_patterns = []
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("found to check")
