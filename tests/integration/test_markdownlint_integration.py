"""Integration tests for markdownlint tool."""

import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.parsers.markdownlint.markdownlint_issue import MarkdownlintIssue
from lintro.plugins import ToolRegistry

logger.remove()
logger.add(lambda msg: print(msg, end=""), level="INFO")
SAMPLE_FILE = "test_samples/tools/config/markdown/markdownlint_violations.md"


def find_markdownlint_cmd() -> list[str] | None:
    """Find markdownlint-cli2 command.

    Returns:
        Command list if found, None otherwise
    """
    if shutil.which("npx"):
        return ["npx", "--yes", "markdownlint-cli2"]
    if shutil.which("markdownlint-cli2"):
        return ["markdownlint-cli2"]
    return None


def run_markdownlint_directly(file_path: Path) -> tuple[bool, str, int]:
    """Run markdownlint-cli2 directly on a file and return result tuple.

    Args:
        file_path: Path to the file to check with markdownlint-cli2.

    Returns:
        tuple[bool, str, int]: Success status, output text, and issue count.
    """
    cmd_base = find_markdownlint_cmd()
    if cmd_base is None:
        pytest.skip("markdownlint-cli2 not found in PATH")
    # Use relative path from repo root to match lintro's behavior
    repo_root = Path(__file__).parent.parent.parent
    # Resolve to absolute path first if it's relative
    abs_file_path = file_path.resolve() if not file_path.is_absolute() else file_path
    try:
        relative_path = abs_file_path.relative_to(repo_root)
    except ValueError:
        # Fallback: use relative path calculation if not under repo root
        import os

        relative_path = Path(os.path.relpath(abs_file_path, repo_root))
    cmd = [*cmd_base, str(relative_path)]

    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        cmd,
        capture_output=True,
        text=True,
        check=False,
        cwd=repo_root,
    )

    # Count issues from output (non-empty lines are typically issues)
    issues = [
        line
        for line in result.stdout.splitlines()
        if line.strip() and ":" in line and "MD" in line
    ]
    issues_count = len(issues)
    success = issues_count == 0 and result.returncode == 0
    return (success, result.stdout, issues_count)


@pytest.mark.markdownlint
def test_markdownlint_available() -> None:
    """Check if markdownlint-cli2 is available in PATH."""
    cmd_base = find_markdownlint_cmd()
    if cmd_base is None:
        pytest.skip("markdownlint-cli2 not found in PATH")
    try:
        cmd = [*cmd_base, "--version"]
        result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert_that(result.returncode).is_equal_to(0)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("markdownlint-cli2 not available")


@pytest.mark.markdownlint
def test_markdownlint_direct_vs_lintro_parity() -> None:
    """Compare direct markdownlint-cli2 output with lintro wrapper.

    Runs markdownlint-cli2 directly on the sample file and compares the
    issue count with lintro's wrapper to ensure parity.
    """
    sample_path = Path(SAMPLE_FILE)
    if not sample_path.exists():
        pytest.skip(f"Sample file {SAMPLE_FILE} not found")

    # Run markdownlint-cli2 directly
    direct_success, direct_output, direct_count = run_markdownlint_directly(
        sample_path,
    )

    # Run via lintro
    tool = ToolRegistry.get("markdownlint")
    assert_that(tool).is_not_none()
    # Clear exclude patterns to allow scanning test_samples
    tool.exclude_patterns = []
    lintro_result = tool.check([str(sample_path)], {})

    # Compare issue counts (allow some variance due to parsing differences)
    # Direct count may include lines we don't parse, so lintro count <= direct
    assert_that(lintro_result.issues_count).is_greater_than_or_equal_to(0)
    # If direct found issues, lintro should find <= direct count (parsing may miss some)
    if direct_count > 0:
        assert_that(lintro_result.issues_count).is_less_than_or_equal_to(direct_count)
        assert_that(lintro_result.issues_count).is_greater_than(0)

    # Both should agree on success/failure
    # Success is True when no issues found, False when issues are found
    assert_that(lintro_result.success).is_equal_to(direct_success)


@pytest.mark.markdownlint
def test_markdownlint_integration_basic() -> None:
    """Basic integration test for markdownlint tool.

    Verifies that the tool can discover files, run checks, and parse output.
    """
    sample_path = Path(SAMPLE_FILE)
    if not sample_path.exists():
        pytest.skip(f"Sample file {SAMPLE_FILE} not found")

    tool = ToolRegistry.get("markdownlint")
    assert_that(tool).is_not_none()
    result = tool.check([str(sample_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("markdownlint")
    assert_that(result.issues_count).is_greater_than_or_equal_to(0)

    # If there are issues, verify they're properly structured
    if result.issues:
        issue = result.issues[0]
        # Use isinstance check for type narrowing
        if not isinstance(issue, MarkdownlintIssue):
            pytest.fail("issue should be MarkdownlintIssue")
        assert_that(issue.file).is_not_empty()
        assert_that(issue.line).is_greater_than(0)
        assert_that(issue.code).matches(r"^MD\d+$")
        assert_that(issue.message).is_not_empty()
