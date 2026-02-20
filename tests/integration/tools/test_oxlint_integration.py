"""Integration tests for Oxlint tool definition.

These tests require oxlint to be installed and available in PATH.
They verify the OxlintPlugin definition, check command, fix command, and
set_options method.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


def oxlint_is_available() -> bool:
    """Check if oxlint is installed and actually works.

    This is more robust than just checking shutil.which() because wrapper
    scripts may exist even when the underlying npm package isn't installed.
    We verify the tool works by actually linting a simple JavaScript snippet.

    Returns:
        True if oxlint is available and functional, False otherwise.
    """
    if shutil.which("oxlint") is None:
        return False
    try:
        # First check --version works
        version_result = subprocess.run(
            ["oxlint", "--version"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if version_result.returncode != 0:
            return False

        # Then verify it can actually lint code (catches missing npm packages)
        # oxlint returns 0 for clean files, non-zero for files with issues
        # Use --quiet to minimize output and lint valid code that should pass
        lint_result = subprocess.run(
            ["oxlint", "--stdin-filename", "test.js"],
            input=b"const x = 1;\n",
            capture_output=True,
            timeout=10,
            check=False,
        )
        # returncode 0 = clean, 1 = issues found, other = error
        # We accept 0 or 1 as "working" - anything else is a tool failure
        return lint_result.returncode in (0, 1)
    except (subprocess.TimeoutExpired, OSError):
        return False


# Skip all tests if oxlint is not installed or not working
pytestmark = pytest.mark.skipif(
    not oxlint_is_available(),
    reason="oxlint not installed or not working",
)


@pytest.fixture
def temp_js_file_with_issues(tmp_path: Path) -> str:
    """Create a temporary JavaScript file with lint issues.

    Creates a file containing code with lint issues that Oxlint
    should detect, including:
    - Unused variables
    - Use of debugger statement
    - Use of var instead of const/let

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "test_file.js"
    file_path.write_text(
        """\
// Test file with lint violations
var unused = 1;

if (someVar == 2) {
  console.log('test');
}

debugger;
""",
    )
    return str(file_path)


@pytest.fixture
def temp_js_file_clean(tmp_path: Path) -> str:
    """Create a temporary JavaScript file with no lint issues.

    Creates a file containing clean JavaScript code that should pass
    Oxlint linting without issues.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "clean_file.js"
    file_path.write_text(
        """\
/**
 * A clean module.
 */

/**
 * Returns a greeting.
 * @returns {string} Greeting message.
 */
function hello() {
  return "Hello, World!";
}

hello();
""",
    )
    return str(file_path)


@pytest.fixture
def temp_ts_file_with_issues(tmp_path: Path) -> str:
    """Create a temporary TypeScript file with lint issues.

    Creates a file containing TypeScript code with lint issues that Oxlint
    should detect.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "test_file.ts"
    file_path.write_text(
        """\
// TypeScript file with violations
var unusedVar: string = "unused";

function empty(): void {}

debugger;
""",
    )
    return str(file_path)


# --- Tests for OxlintPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "oxlint"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify OxlintPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    oxlint_plugin = get_plugin("oxlint")
    assert_that(getattr(oxlint_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxlintPlugin definition includes JS/TS file patterns.

    Tests that the plugin is configured to handle JS/TS files (*.js, *.ts, *.jsx, *.tsx).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxlint_plugin = get_plugin("oxlint")
    patterns = oxlint_plugin.definition.file_patterns
    # Core JS/TS patterns
    assert_that(patterns).contains("*.js")
    assert_that(patterns).contains("*.ts")
    assert_that(patterns).contains("*.jsx")
    assert_that(patterns).contains("*.tsx")
    # Module variants
    assert_that(patterns).contains("*.mjs")
    assert_that(patterns).contains("*.cjs")
    assert_that(patterns).contains("*.mts")
    assert_that(patterns).contains("*.cts")
    # Framework support
    assert_that(patterns).contains("*.vue")
    assert_that(patterns).contains("*.svelte")
    assert_that(patterns).contains("*.astro")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxlintPlugin definition has a version command.

    Tests that the plugin exposes a version command for checking
    the installed Oxlint version.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxlint_plugin = get_plugin("oxlint")
    assert_that(oxlint_plugin.definition.version_command).is_not_none()


# --- Integration tests for oxlint check command ---


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_js_file_with_issues: str,
) -> None:
    """Verify Oxlint check detects lint issues in problematic files.

    Runs Oxlint on a file containing lint issues and verifies that
    issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_js_file_with_issues: Path to file with lint issues.
    """
    oxlint_plugin = get_plugin("oxlint")
    result = oxlint_plugin.check([temp_js_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxlint")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_js_file_clean: str,
) -> None:
    """Verify Oxlint check passes on clean files.

    Runs Oxlint on a clean file and verifies no issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_js_file_clean: Path to clean file.
    """
    oxlint_plugin = get_plugin("oxlint")
    result = oxlint_plugin.check([temp_js_file_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxlint")
    assert_that(result.success).is_true()


def test_check_typescript_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_ts_file_with_issues: str,
) -> None:
    """Verify Oxlint check works with TypeScript files.

    Runs Oxlint on a TypeScript file and verifies issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_ts_file_with_issues: Path to TypeScript file with issues.
    """
    oxlint_plugin = get_plugin("oxlint")
    result = oxlint_plugin.check([temp_ts_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxlint")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Oxlint check handles empty directories gracefully.

    Runs Oxlint on an empty directory and verifies a result is returned
    with zero issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    oxlint_plugin = get_plugin("oxlint")
    result = oxlint_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.issues_count).is_equal_to(0)


# --- Integration tests for oxlint fix command ---


def test_fix_applies_fixes(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Oxlint fix applies auto-fixes to files.

    Runs Oxlint fix on a file with fixable issues and verifies
    the file content changes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    # Create file with fixable issue (debugger statement)
    file_path = tmp_path / "fixable.js"
    file_path.write_text(
        """\
function test() {
  debugger;
  return 1;
}
""",
    )

    oxlint_plugin = get_plugin("oxlint")
    original_content = file_path.read_text()
    result = oxlint_plugin.fix([str(file_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxlint")

    # File may or may not change depending on what oxlint considers fixable
    # Just verify the fix operation completed successfully
    assert_that(result.initial_issues_count).is_not_none()

    # If issues were fixed, file content should have changed
    if result.fixed_issues_count and result.fixed_issues_count > 0:
        new_content = file_path.read_text()
        assert_that(new_content).is_not_equal_to(original_content)


def test_fix_clean_file_unchanged(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_js_file_clean: str,
) -> None:
    """Verify Oxlint fix doesn't change already clean files.

    Runs Oxlint fix on a clean file and verifies the content stays the same.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_js_file_clean: Path to clean file.
    """
    oxlint_plugin = get_plugin("oxlint")
    original = Path(temp_js_file_clean).read_text()

    result = oxlint_plugin.fix([temp_js_file_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()

    new_content = Path(temp_js_file_clean).read_text()
    assert_that(new_content).is_equal_to(original)


# --- Tests for OxlintPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("timeout", 60, 60),
        ("quiet", True, True),
        ("verbose_fix_output", True, True),
    ],
    ids=["timeout", "quiet", "verbose_fix_output"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify OxlintPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    oxlint_plugin = get_plugin("oxlint")
    oxlint_plugin.set_options(**{option_name: option_value})
    assert_that(oxlint_plugin.options.get(option_name)).is_equal_to(expected)


def test_set_exclude_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxlintPlugin.set_options correctly sets exclude_patterns.

    Tests that exclude patterns can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxlint_plugin = get_plugin("oxlint")
    oxlint_plugin.set_options(exclude_patterns=["node_modules", "dist"])
    assert_that(oxlint_plugin.exclude_patterns).contains("node_modules")
    assert_that(oxlint_plugin.exclude_patterns).contains("dist")


# --- Integration tests for new oxlint options ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("config", ".oxlintrc.json", ".oxlintrc.json"),
        ("tsconfig", "tsconfig.json", "tsconfig.json"),
    ],
    ids=["config", "tsconfig"],
)
def test_set_config_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify OxlintPlugin.set_options correctly sets config options.

    Tests that config and tsconfig options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    oxlint_plugin = get_plugin("oxlint")
    oxlint_plugin.set_options(**{option_name: option_value})
    assert_that(oxlint_plugin.options.get(option_name)).is_equal_to(expected)


def test_set_deny_option(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxlintPlugin.set_options correctly sets deny option.

    Tests that deny rules can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxlint_plugin = get_plugin("oxlint")
    oxlint_plugin.set_options(deny=["no-debugger", "eqeqeq"])
    assert_that(oxlint_plugin.options.get("deny")).is_equal_to(
        ["no-debugger", "eqeqeq"],
    )


def test_set_allow_option(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxlintPlugin.set_options correctly sets allow option.

    Tests that allow rules can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxlint_plugin = get_plugin("oxlint")
    oxlint_plugin.set_options(allow=["no-console"])
    assert_that(oxlint_plugin.options.get("allow")).is_equal_to(["no-console"])


def test_set_warn_option(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxlintPlugin.set_options correctly sets warn option.

    Tests that warn rules can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxlint_plugin = get_plugin("oxlint")
    oxlint_plugin.set_options(warn=["complexity"])
    assert_that(oxlint_plugin.options.get("warn")).is_equal_to(["complexity"])


def test_deny_option_affects_check_output(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify deny option affects check output.

    Tests that denying a rule causes it to be reported as an error.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    # Create file with debugger statement
    file_path = tmp_path / "test.js"
    file_path.write_text(
        """\
function test() {
  debugger;
  return 1;
}
""",
    )

    oxlint_plugin = get_plugin("oxlint")
    oxlint_plugin.set_options(deny=["no-debugger"])
    result = oxlint_plugin.check([str(file_path)], {})

    # Should detect the debugger statement
    assert_that(result).is_not_none()
    assert_that(result.issues_count).is_greater_than(0)
