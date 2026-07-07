"""Integration tests for the ktlint tool definition.

These tests require ktlint (and a JVM) to be installed and available in PATH.
They exercise the real KtlintPlugin against Kotlin fixtures, verifying check
detection, the clean path, and the fix invariant
(``initial = fixed + remaining``) for both a fully auto-correctable file and a
file with a non-auto-correctable violation remaining.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.definitions.ktlint import KtlintPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("ktlint") is None,
    reason="ktlint not installed",
)

SAMPLE_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "test_samples"
    / "tools"
    / "kotlin"
    / "ktlint"
)
VIOLATIONS_KT = SAMPLE_DIR / "ktlint_violations.kt"
VIOLATIONS_KTS = SAMPLE_DIR / "ktlint_violations.kts"
CLEAN_KT = SAMPLE_DIR / "Clean.kt"


@pytest.fixture
def plugin() -> KtlintPlugin:
    """Provide a real KtlintPlugin instance.

    Returns:
        KtlintPlugin: A plugin instance for integration testing.
    """
    return KtlintPlugin()


def _copy(src: Path, tmp_path: Path, name: str) -> str:
    """Copy a sample file into ``tmp_path`` under ``name``.

    Args:
        src: Source fixture path.
        tmp_path: Destination temporary directory.
        name: Destination file name.

    Returns:
        The destination path as a string.
    """
    dst = tmp_path / name
    dst.write_text(src.read_text())
    return str(dst)


def test_definition_reports_version(plugin: KtlintPlugin) -> None:
    """The plugin advertises fix support and the expected patterns.

    Args:
        plugin: The plugin under test.
    """
    assert_that(plugin.definition.name).is_equal_to("ktlint")
    assert_that(plugin.definition.can_fix).is_true()
    assert_that(plugin.definition.file_patterns).contains("*.kt", "*.kts")


def test_check_detects_violations(plugin: KtlintPlugin, tmp_path: Path) -> None:
    """Detect style violations in a Kotlin source file.

    Args:
        plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    # Named after its class so the filename rule does not fire; the remaining
    # violations are formatting issues.
    target = _copy(VIOLATIONS_KT, tmp_path, "Example.kt")

    result = plugin.check([target], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that([issue.rule for issue in result.issues]).contains(
        "standard:op-spacing",
    )


def test_check_clean_file(plugin: KtlintPlugin, tmp_path: Path) -> None:
    """A clean Kotlin file passes the check.

    Args:
        plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    target = _copy(CLEAN_KT, tmp_path, "Clean.kt")

    result = plugin.check([target], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_kotlin_script(plugin: KtlintPlugin, tmp_path: Path) -> None:
    """Detect violations in a Kotlin Script (.kts) file.

    Args:
        plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    target = _copy(VIOLATIONS_KTS, tmp_path, "build.gradle.kts")

    result = plugin.check([target], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_fix_full_auto_correct(plugin: KtlintPlugin, tmp_path: Path) -> None:
    """All formatting issues are fixed and the invariant holds.

    Args:
        plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    target = _copy(VIOLATIONS_KT, tmp_path, "Example.kt")

    initial = plugin.check([target], {}).issues_count
    result = plugin.fix([target], {})

    assert_that(initial).is_greater_than(0)
    assert_that(result.success).is_true()
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(initial)
    assert_that(result.initial_issues_count).is_equal_to(
        result.fixed_issues_count + result.remaining_issues_count,
    )
    # Re-checking the fixed file reports no issues.
    assert_that(plugin.check([target], {}).issues_count).is_equal_to(0)


def test_fix_partial_leaves_non_correctable(
    plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """A non-auto-correctable rule remains and the invariant still holds.

    The file is deliberately named so it does not match its class name,
    triggering the non-auto-correctable ``standard:filename`` rule.

    Args:
        plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    target = _copy(VIOLATIONS_KT, tmp_path, "lowercase_name.kt")

    initial = plugin.check([target], {}).issues_count
    result = plugin.fix([target], {})

    assert_that(initial).is_greater_than(result.fixed_issues_count)
    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_greater_than(0)
    assert_that(result.initial_issues_count).is_equal_to(initial)
    assert_that(result.initial_issues_count).is_equal_to(
        result.fixed_issues_count + result.remaining_issues_count,
    )
    assert_that([issue.rule for issue in result.issues]).contains(
        "standard:filename",
    )
