"""Integration tests for the PHPStan tool (binary-gated).

These tests exercise the real ``phpstan`` binary. They are skipped when PHP
and PHPStan are not installed, so local runs behave like CI (which ships the
tool in the Docker image) without failing on developer machines that lack the
PHP toolchain.
"""

from __future__ import annotations

from typing import cast

import shutil
import subprocess
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.phpstan.phpstan_issue import PhpstanIssue
from lintro.models.core.tool_result import ToolResult
from lintro.plugins import ToolRegistry

VIOLATIONS_PHP = (
    "<?php\n\n"
    "function add(int $a, int $b): int\n"
    "{\n"
    "    return $a + $b;\n"
    "}\n\n"
    "echo add(1);\n"
    "$result = nonExistentFunction();\n"
    "echo $result;\n"
)

CLEAN_PHP = (
    "<?php\n\n"
    "function multiply(int $a, int $b): int\n"
    "{\n"
    "    return $a * $b;\n"
    "}\n\n"
    "echo multiply(2, 3);\n"
)


def phpstan_available() -> bool:
    """Return True if the ``phpstan`` binary is available on PATH.

    Returns:
        bool: True when ``phpstan --version`` succeeds, False otherwise.
    """
    if shutil.which("phpstan") is None:
        return False
    try:
        proc = subprocess.run(
            ["phpstan", "--version"],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except (FileNotFoundError, OSError):
        return False


@pytest.mark.phpstan
def test_phpstan_available() -> None:
    """Skip the suite when PHPStan is not present locally."""
    if not phpstan_available():
        pytest.skip("phpstan not available")


@pytest.mark.phpstan
def test_phpstan_reports_violations(tmp_path: Path) -> None:
    """Lintro detects the violations PHPStan reports on a seeded file."""
    if not phpstan_available():
        pytest.skip("phpstan not available")
    php = tmp_path / "violations.php"
    php.write_text(VIOLATIONS_PHP)

    tool = ToolRegistry.get("phpstan")
    result: ToolResult = tool.check([str(php)], {})

    assert_that(result.name).is_equal_to("phpstan")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than_or_equal_to(2)
    assert_that(result.issues).is_not_none()
    identifiers = {
        cast(PhpstanIssue, issue).identifier for issue in result.issues  # type: ignore[union-attr]
    }
    assert_that(identifiers).contains("function.notFound")


@pytest.mark.phpstan
def test_phpstan_clean_file_passes(tmp_path: Path) -> None:
    """A clean PHP file passes PHPStan analysis with no issues."""
    if not phpstan_available():
        pytest.skip("phpstan not available")
    php = tmp_path / "clean.php"
    php.write_text(CLEAN_PHP)

    tool = ToolRegistry.get("phpstan")
    result: ToolResult = tool.check([str(php)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


@pytest.mark.phpstan
def test_phpstan_bare_file_does_not_crash(tmp_path: Path) -> None:
    """Analyzing a standalone file without an autoloader must not crash."""
    if not phpstan_available():
        pytest.skip("phpstan not available")
    php = tmp_path / "bare.php"
    php.write_text("<?php\n$greeting = 'hello';\necho $greeting;\n")

    tool = ToolRegistry.get("phpstan")
    result: ToolResult = tool.check([str(php)], {})

    # Whatever the findings, the tool returns a well-formed result object.
    assert_that(isinstance(result, ToolResult)).is_true()
    assert_that(result.name).is_equal_to("phpstan")
