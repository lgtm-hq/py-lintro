"""Binary-gated integration tests for the stylelint tool.

These tests run the real stylelint binary against temporary CSS/SCSS fixtures
with a local config, exercising both the check and fix paths end to end. They
skip automatically when stylelint is not resolvable in the environment.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.stylelint.stylelint_issue import StylelintIssue
from lintro.tools.definitions.stylelint import StylelintPlugin

# Shared fixtures: single source of truth for stylelint sample content.
FIXTURES = Path("test_samples/tools/web/stylelint").resolve()


def _stylelint_available() -> bool:
    """Report whether the stylelint binary is runnable in this environment.

    Returns:
        True if ``stylelint --version`` succeeds via the resolved command.
    """
    plugin = StylelintPlugin()
    cmd = [*plugin._get_executable_command(tool_name="stylelint"), "--version"]
    try:
        # Probe from a neutral cwd: bunx/npx resolution can succeed from the
        # repo root (whose node_modules satisfy the CLI) while failing in the
        # tmp directories the tests actually lint from.
        proc = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv probe of stylelint binary; shell=False, no user shell input
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            cwd=tempfile.gettempdir(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


pytestmark = pytest.mark.skipif(
    not _stylelint_available(),
    reason="stylelint binary not available",
)


@pytest.fixture
def styled_project(tmp_path: Path) -> Path:
    """Create a temp project with a stylelint config.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path: The project directory containing ``.stylelintrc.json``.
    """
    shutil.copy(FIXTURES / ".stylelintrc.json", tmp_path / ".stylelintrc.json")
    return tmp_path


def test_check_reports_css_violations(styled_project: Path) -> None:
    """Check surfaces real violations in a CSS fixture."""
    target = styled_project / "bad.css"
    shutil.copy(FIXTURES / "stylelint_violations.css", target)

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    codes = {
        issue.code
        for issue in (result.issues or [])
        if isinstance(issue, StylelintIssue)
    }
    assert_that(codes).contains("color-hex-length")


def test_check_reports_scss_violations(styled_project: Path) -> None:
    """Check handles nested SCSS syntax and reports violations."""
    target = styled_project / "bad.scss"
    shutil.copy(FIXTURES / "stylelint_violations.scss", target)

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    codes = {
        issue.code
        for issue in (result.issues or [])
        if isinstance(issue, StylelintIssue)
    }
    assert_that(codes).contains("color-hex-length")


def test_check_passes_clean_css(styled_project: Path) -> None:
    """A clean CSS file produces no issues."""
    target = styled_project / "clean.css"
    shutil.copy(FIXTURES / "stylelint_clean.css", target)

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_fix_applies_and_preserves_invariant(styled_project: Path) -> None:
    """Fix auto-corrects fixable issues and preserves the count invariant."""
    target = styled_project / "fixme.css"
    target.write_text("a {\n  color: #FFFFFF;\n}\n.empty {\n}\n")

    plugin = StylelintPlugin()
    result = plugin.fix([str(target)], {})

    # color-hex-length is auto-fixable; block-no-empty is not.
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_greater_than(0)
    fixed = result.fixed_issues_count or 0
    remaining = result.remaining_issues_count or 0
    assert_that(fixed + remaining).is_equal_to(result.initial_issues_count)
    assert_that(target.read_text()).contains("#FFF")
    assert_that(target.read_text()).does_not_contain("#FFFFFF")


def test_skips_without_config(tmp_path: Path) -> None:
    """Without a config, stylelint is skipped as a non-error."""
    target = tmp_path / "bad.css"
    target.write_text("a {\n  color: #FFFFFF;\n}\n")

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("no stylelint configuration")
