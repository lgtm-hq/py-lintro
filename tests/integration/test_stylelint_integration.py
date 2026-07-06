"""Binary-gated integration tests for the stylelint tool.

These tests run the real stylelint binary against temporary CSS/SCSS fixtures
with a local config, exercising both the check and fix paths end to end. They
skip automatically when stylelint is not resolvable in the environment.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.definitions.stylelint import StylelintPlugin

STYLELINT_CONFIG = (
    '{ "rules": { "color-hex-length": "short", "block-no-empty": true, '
    '"declaration-block-no-duplicate-properties": true } }'
)


def _stylelint_available() -> bool:
    """Report whether the stylelint binary is runnable in this environment.

    Returns:
        True if ``stylelint --version`` succeeds via the resolved command.
    """
    plugin = StylelintPlugin()
    cmd = plugin._get_executable_command(tool_name="stylelint") + ["--version"]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
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
    (tmp_path / ".stylelintrc.json").write_text(STYLELINT_CONFIG)
    return tmp_path


def test_check_reports_css_violations(styled_project: Path) -> None:
    """Check surfaces real violations in a CSS fixture."""
    target = styled_project / "bad.css"
    target.write_text("a {\n  color: #FFFFFF;\n  color: #FFFFFF;\n}\n")

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    codes = {issue.code for issue in (result.issues or [])}
    assert_that(codes).contains("color-hex-length")


def test_check_reports_scss_violations(styled_project: Path) -> None:
    """Check handles nested SCSS syntax and reports violations."""
    target = styled_project / "bad.scss"
    target.write_text(
        ".card {\n  color: #AABBCC;\n\n  .title {\n    color: #AABBCC;\n  }\n}\n",
    )

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    codes = {issue.code for issue in (result.issues or [])}
    assert_that(codes).contains("color-hex-length")


def test_check_passes_clean_css(styled_project: Path) -> None:
    """A clean CSS file produces no issues."""
    target = styled_project / "clean.css"
    target.write_text("a {\n  color: #fff;\n}\n")

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
    assert_that(
        result.fixed_issues_count + result.remaining_issues_count,
    ).is_equal_to(result.initial_issues_count)
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
