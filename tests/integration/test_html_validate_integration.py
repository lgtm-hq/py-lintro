"""Integration tests for the html-validate tool.

These tests are gated on html-validate being resolvable (via bunx/npx or a
direct binary). They exercise the plugin end-to-end against a real HTML fixture.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue
from lintro.plugins import ToolRegistry

SAMPLE_FILE = "test_samples/tools/web/html_validate/html_validate_violations.html"


def find_html_validate_cmd() -> list[str] | None:
    """Resolve a command that runs html-validate.

    Returns:
        Command list if html-validate is resolvable, None otherwise.
    """
    if shutil.which("html-validate"):
        return ["html-validate"]
    if shutil.which("bunx"):
        return ["bunx", "html-validate"]
    if shutil.which("npx"):
        return ["npx", "--yes", "html-validate"]
    return None


def _html_validate_runnable() -> bool:
    """Report whether html-validate can actually be invoked.

    Returns:
        True if ``html-validate --version`` succeeds, False otherwise.
    """
    cmd_base = find_html_validate_cmd()
    if cmd_base is None:
        return False
    try:
        # Probe from a neutral cwd: the tests lint files in tmp directories,
        # and bunx/npx resolution can succeed from the repo root (whose
        # node_modules satisfy the CLI's dependencies) while failing anywhere
        # else. Probing from the repo would let the tests run a broken tool.
        result = subprocess.run(
            [*cmd_base, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            cwd=tempfile.gettempdir(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


@pytest.mark.html_validate
def test_html_validate_available() -> None:
    """html-validate resolves and reports a version."""
    if not _html_validate_runnable():
        pytest.skip("html-validate not available")
    cmd_base = find_html_validate_cmd()
    assert_that(cmd_base).is_not_none()


@pytest.mark.html_validate
def test_html_validate_detects_violations() -> None:
    """The plugin detects violations in the sample HTML fixture."""
    if not _html_validate_runnable():
        pytest.skip("html-validate not available")

    sample_path = Path(SAMPLE_FILE)
    if not sample_path.exists():
        pytest.skip(f"Sample file {SAMPLE_FILE} not found")

    tool = ToolRegistry.get("html_validate")
    assert_that(tool).is_not_none()
    tool.exclude_patterns = []
    result = tool.check([str(sample_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("html_validate")
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.success).is_false()

    issue = result.issues[0]
    if not isinstance(issue, HtmlValidateIssue):
        pytest.fail("issue should be HtmlValidateIssue")
    assert_that(issue.file).is_not_empty()
    assert_that(issue.code).is_not_empty()
    assert_that(issue.message).is_not_empty()
    assert_that(issue.severity).is_in("error", "warning")


@pytest.mark.html_validate
def test_html_validate_clean_file_passes(tmp_path: Path) -> None:
    """A valid HTML fragment yields no issues under default (no-config) rules.

    Args:
        tmp_path: Pytest-managed temporary directory (keeps the transient
            fixture out of the versioned ``test_samples`` tree, which other
            tests may scan concurrently).
    """
    if not _html_validate_runnable():
        pytest.skip("html-validate not available")

    clean_file = tmp_path / "clean_fragment.html"
    clean_file.write_text("<p>Hello world</p>\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(clean_file)], {})
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()
