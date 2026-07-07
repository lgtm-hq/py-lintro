"""Integration tests for the swiftlint tool definition.

These tests require SwiftLint to be installed and available on PATH. They
exercise the real ``swiftlint`` binary against Swift fixtures for both the
check and fix paths, including the ``initial == fixed + remaining`` invariant.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if swiftlint is not installed.
pytestmark = pytest.mark.skipif(
    shutil.which("swiftlint") is None,
    reason="swiftlint not installed",
)


@pytest.fixture
def swift_file_with_issues(tmp_path: Path) -> str:
    """Create a Swift file containing several SwiftLint violations.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "Violations.swift"
    file_path.write_text(
        "import Foundation\n"
        "\n"
        "func doStuff() {\n"
        "    let x = 1 ;\n"
        '    print("hello world this is a very long line that exceeds the '
        "default line length limit set by swiftlint rules for sure yes ok"
        '")\n'
        "    print(x)\n"
        "}\n"
        "\n"
        "class foo {}\n",
    )
    return str(file_path)


@pytest.fixture
def swift_file_clean(tmp_path: Path) -> str:
    """Create a Swift file that passes SwiftLint's default rules.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "Clean.swift"
    file_path.write_text(
        'import Foundation\n\nlet greeting = "hello"\nprint(greeting)\n',
    )
    return str(file_path)


def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify the plugin definition exposes the expected identity.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("swiftlint")
    assert_that(plugin.definition.name).is_equal_to("swiftlint")
    assert_that(plugin.definition.can_fix).is_true()


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify the plugin targets Swift files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("swiftlint")
    assert_that(plugin.definition.file_patterns).contains("*.swift")


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    swift_file_with_issues: str,
) -> None:
    """Check detects violations in a problematic Swift file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swift_file_with_issues: Path to a file with violations.
    """
    plugin = get_plugin("swiftlint")
    result = plugin.check([swift_file_with_issues], {})

    assert_that(result.name).is_equal_to("swiftlint")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    codes = {issue.code for issue in result.issues}
    assert_that(codes).contains("line_length")


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    swift_file_clean: str,
) -> None:
    """Check reports no issues for a clean Swift file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swift_file_clean: Path to a clean file.
    """
    plugin = get_plugin("swiftlint")
    result = plugin.check([swift_file_clean], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Check handles a directory with no Swift files gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Temporary directory with no Swift files.
    """
    plugin = get_plugin("swiftlint")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_fix_invariant_holds(
    get_plugin: Callable[[str], BaseToolPlugin],
    swift_file_with_issues: str,
) -> None:
    """Fix satisfies ``initial == fixed + remaining`` on a real run.

    The fixture contains at least one auto-correctable violation
    (``trailing_semicolon``) and some that are not, so the fix run should
    report both a non-zero fixed count and a non-zero remaining count.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swift_file_with_issues: Path to a file with violations.
    """
    plugin = get_plugin("swiftlint")

    # Capture the initial issue count via a check first.
    initial = plugin.check([swift_file_with_issues], {}).issues_count
    assert_that(initial).is_greater_than(0)

    result = plugin.fix([swift_file_with_issues], {})

    assert_that(result.initial_issues_count).is_equal_to(
        result.fixed_issues_count + result.remaining_issues_count,
    )
    assert_that(result.initial_issues_count).is_equal_to(initial)
    assert_that(result.fixed_issues_count).is_greater_than(0)


def test_fix_clean_file_unchanged(
    get_plugin: Callable[[str], BaseToolPlugin],
    swift_file_clean: str,
) -> None:
    """Fix leaves an already-clean Swift file untouched.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swift_file_clean: Path to a clean file.
    """
    plugin = get_plugin("swiftlint")
    original = Path(swift_file_clean).read_text()

    result = plugin.fix([swift_file_clean], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(Path(swift_file_clean).read_text()).is_equal_to(original)


def test_set_options_timeout(
    get_plugin: Callable[[str], BaseToolPlugin],
    swift_file_with_issues: str,
) -> None:
    """set_options accepts a timeout and the check still runs.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swift_file_with_issues: Path to a file with violations.
    """
    plugin = get_plugin("swiftlint")
    plugin.set_options(timeout=90)
    result = plugin.check([swift_file_with_issues], {})

    assert_that(result.name).is_equal_to("swiftlint")
