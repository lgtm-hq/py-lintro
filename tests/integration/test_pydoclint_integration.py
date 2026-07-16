"""Integration tests for pydoclint core.

Note: The simplified pydoclint plugin reads configuration from [tool.pydoclint]
in pyproject.toml. See docs/tool-analysis/pydoclint-analysis.md for settings.
"""

import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.plugins import ToolRegistry

logger.remove()
logger.add(lambda msg: print(msg, end=""), level="INFO")

SAMPLE_FILE = "test_samples/tools/python/pydoclint/pydoclint_violations.py"


def run_pydoclint_directly(file_path: Path) -> tuple[bool, str, int]:
    """Run pydoclint directly on a file and return result tuple.

    Args:
        file_path: Path to the file to check with pydoclint.

    Returns:
        tuple[bool, str, int]: A tuple of (success, output, issues_count).
    """
    cmd = [
        "pydoclint",
        "--quiet",
        str(file_path),
    ]
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    issues = [line for line in output.splitlines() if "DOC" in line and ":" in line]
    issues_count = len(issues)
    success = issues_count == 0 and result.returncode == 0
    return success, output, issues_count


def _ensure_pydoclint_available() -> None:
    """Skip test if pydoclint CLI is not runnable.

    Attempts to execute `pydoclint --version` to verify that the CLI exists
    and is runnable in the current environment.
    """
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["pydoclint", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("pydoclint CLI not working; skipping direct CLI test")
    except FileNotFoundError:
        pytest.skip("pydoclint CLI not installed; skipping direct CLI test")


def test_pydoclint_reports_violations_direct(tmp_path: Path) -> None:
    """Pydoclint CLI: Should detect and report violations in a sample file.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    _ensure_pydoclint_available()
    sample_file = tmp_path / "pydoclint_violations.py"
    shutil.copy(SAMPLE_FILE, sample_file)
    logger.info("[TEST] Running pydoclint directly on sample file...")
    success, output, issues = run_pydoclint_directly(sample_file)
    logger.info(f"[LOG] Pydoclint found {issues} issues. Output:\n{output}")
    assert_that(success).is_false().described_as(
        "Pydoclint should fail when violations are present.",
    )
    assert_that(issues).is_greater_than(0).described_as(
        "Pydoclint should report at least one issue.",
    )
    assert_that(output).contains("DOC").described_as(
        "Pydoclint output should contain error codes.",
    )


def test_pydoclint_reports_violations_through_lintro(tmp_path: Path) -> None:
    """Lintro PydoclintTool: Should detect and report violations in a sample file.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    _ensure_pydoclint_available()
    sample_file = tmp_path / "pydoclint_violations.py"
    shutil.copy(SAMPLE_FILE, sample_file)
    logger.info(f"SAMPLE_FILE: {sample_file}, exists: {sample_file.exists()}")
    logger.info("[TEST] Running PydoclintTool through lintro on sample file...")
    tool = ToolRegistry.get("pydoclint")
    assert_that(tool).is_not_none()
    result = tool.check([str(sample_file)], {})
    logger.info(
        f"[LOG] Lintro PydoclintTool found {result.issues_count} issues. "
        f"Output:\n{result.output}",
    )
    assert_that(result.success).is_false().described_as(
        "Lintro PydoclintTool should fail when violations are present.",
    )
    assert_that(result.issues_count).is_greater_than(0).described_as(
        "Lintro PydoclintTool should report at least one issue.",
    )


def test_pydoclint_output_consistency_direct_vs_lintro(tmp_path: Path) -> None:
    """Pydoclint CLI vs Lintro: Should produce consistent results for the same file.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    _ensure_pydoclint_available()
    sample_file = tmp_path / "pydoclint_violations.py"
    shutil.copy(SAMPLE_FILE, sample_file)
    logger.info("[TEST] Comparing pydoclint CLI and Lintro PydoclintTool outputs...")
    tool = ToolRegistry.get("pydoclint")
    assert_that(tool).is_not_none()
    direct_success, direct_output, direct_issues = run_pydoclint_directly(sample_file)
    result = tool.check([str(sample_file)], {})
    logger.info(
        f"[LOG] CLI issues: {direct_issues}, Lintro issues: {result.issues_count}",
    )
    assert_that(direct_success).is_equal_to(result.success).described_as(
        "Success/failure mismatch between CLI and Lintro.",
    )
    # Issue count may differ slightly due to parsing differences
    # But both should find issues
    assert_that(direct_issues).is_greater_than(0)
    assert_that(result.issues_count).is_greater_than(0)


def test_pydoclint_fix_method_not_implemented(tmp_path: Path) -> None:
    """Lintro PydoclintTool: .fix() should raise NotImplementedError.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    sample_file = tmp_path / "pydoclint_violations.py"
    shutil.copy(SAMPLE_FILE, sample_file)
    logger.info(
        "[TEST] Verifying that PydoclintTool.fix() raises NotImplementedError...",
    )
    tool = ToolRegistry.get("pydoclint")
    assert_that(tool).is_not_none()
    with pytest.raises(NotImplementedError):
        tool.fix([str(sample_file)], {})
    logger.info("[LOG] NotImplementedError correctly raised by PydoclintTool.fix().")


def test_pydoclint_clean_file_passes(tmp_path: Path) -> None:
    """Lintro PydoclintTool: Should pass on a clean file.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    _ensure_pydoclint_available()
    clean_file = tmp_path / "clean_module.py"
    # Clean file following Google style with types in annotations, not docstrings
    clean_file.write_text(
        '''"""Clean module with proper docstrings."""


def add_numbers(a: int, b: int) -> int:
    """Add two numbers together.

    Args:
        a: The first number.
        b: The second number.

    Returns:
        The sum of a and b.
    """
    return a + b
''',
    )
    logger.info("[TEST] Running PydoclintTool on a clean file...")
    tool = ToolRegistry.get("pydoclint")
    assert_that(tool).is_not_none()
    result = tool.check([str(clean_file)], {})
    logger.info(f"[LOG] Result: success={result.success}, issues={result.issues_count}")
    assert_that(result.success).is_true().described_as(
        "Lintro PydoclintTool should pass on a clean file.",
    )
    assert_that(result.issues_count).is_equal_to(0).described_as(
        "Lintro PydoclintTool should find no issues in a clean file.",
    )
