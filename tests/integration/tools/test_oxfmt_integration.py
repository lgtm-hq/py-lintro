"""Integration tests for Oxfmt tool definition.

These tests require oxfmt to be installed and available in PATH.
They verify the OxfmtPlugin definition, check command, fix command, and
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


def oxfmt_is_available() -> bool:
    """Check if oxfmt is installed and actually works.

    This is more robust than just checking shutil.which() because wrapper
    scripts may exist even when the underlying npm package isn't installed.
    We verify the tool works by actually formatting a simple JavaScript snippet.

    Returns:
        True if oxfmt is available and functional, False otherwise.
    """
    if shutil.which("oxfmt") is None:
        return False
    try:
        # First check --version works
        version_result = subprocess.run(
            ["oxfmt", "--version"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if version_result.returncode != 0:
            return False

        # Then verify it can actually format code (catches missing npm packages)
        format_result = subprocess.run(
            ["oxfmt", "--stdin-filepath", "test.js"],
            input=b"const x=1;\n",
            capture_output=True,
            timeout=10,
            check=False,
        )
        return format_result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


# Skip all tests if oxfmt is not installed or not working
pytestmark = pytest.mark.skipif(
    not oxfmt_is_available(),
    reason="oxfmt not installed or not working",
)


@pytest.fixture
def temp_js_file_unformatted(tmp_path: Path) -> str:
    """Create a temporary JavaScript file with formatting issues.

    Creates a file containing code with formatting issues that Oxfmt
    should fix, including:
    - Missing spaces around operators
    - Missing spaces after commas
    - Missing newlines

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "test_file.js"
    file_path.write_text(
        """\
function foo(x,y,z){return x+y+z}
const obj={a:1,b:2,c:3}
""",
    )
    return str(file_path)


@pytest.fixture
def temp_js_file_formatted(tmp_path: Path) -> str:
    """Create a temporary JavaScript file that is already formatted.

    Creates a file and formats it with oxfmt to ensure it passes checking
    regardless of oxfmt version differences.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "formatted_file.js"
    # Write unformatted code first
    file_path.write_text(
        """\
function foo(x,y,z){return x+y+z}
const obj={a:1,b:2,c:3}
""",
    )
    # Format it with oxfmt so it's guaranteed to be "formatted" for this version
    subprocess.run(
        ["oxfmt", "--write", str(file_path)],
        check=True,
        capture_output=True,
    )
    return str(file_path)


@pytest.fixture
def temp_ts_file_unformatted(tmp_path: Path) -> str:
    """Create a temporary TypeScript file with formatting issues.

    Creates a file containing TypeScript code with formatting issues that Oxfmt
    should fix.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "test_file.ts"
    file_path.write_text(
        """\
interface User{name:string;age:number}
const user:User={name:"John",age:30}
""",
    )
    return str(file_path)


# --- Tests for OxfmtPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "oxfmt"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify OxfmtPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    assert_that(getattr(oxfmt_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxfmtPlugin definition includes JavaScript/TypeScript/Vue file patterns.

    Tests that the plugin is configured to handle JS/TS and Vue files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    assert_that(oxfmt_plugin.definition.file_patterns).contains("*.js")
    assert_that(oxfmt_plugin.definition.file_patterns).contains("*.ts")
    assert_that(oxfmt_plugin.definition.file_patterns).contains("*.jsx")
    assert_that(oxfmt_plugin.definition.file_patterns).contains("*.tsx")
    assert_that(oxfmt_plugin.definition.file_patterns).contains("*.vue")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxfmtPlugin definition has a version command.

    Tests that the plugin exposes a version command for checking
    the installed Oxfmt version.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    assert_that(oxfmt_plugin.definition.version_command).is_not_none()


# --- Integration tests for oxfmt check command ---


def test_check_file_unformatted(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_js_file_unformatted: str,
) -> None:
    """Verify Oxfmt check detects formatting issues in unformatted files.

    Runs Oxfmt on a file containing formatting issues and verifies that
    issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_js_file_unformatted: Path to file with formatting issues.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    result = oxfmt_plugin.check([temp_js_file_unformatted], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxfmt")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_file_formatted(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_js_file_formatted: str,
) -> None:
    """Verify Oxfmt check passes on properly formatted files.

    Runs Oxfmt on a clean file and verifies no issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_js_file_formatted: Path to formatted file.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    result = oxfmt_plugin.check([temp_js_file_formatted], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxfmt")
    assert_that(result.success).is_true()


def test_check_typescript_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_ts_file_unformatted: str,
) -> None:
    """Verify Oxfmt check works with TypeScript files.

    Runs Oxfmt on a TypeScript file and verifies issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_ts_file_unformatted: Path to TypeScript file with formatting issues.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    result = oxfmt_plugin.check([temp_ts_file_unformatted], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxfmt")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Oxfmt check handles empty directories gracefully.

    Runs Oxfmt on an empty directory and verifies a result is returned
    with zero issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    result = oxfmt_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.issues_count).is_equal_to(0)


# --- Integration tests for oxfmt fix command ---


def test_fix_formats_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_js_file_unformatted: str,
) -> None:
    """Verify Oxfmt fix reformats files with formatting issues.

    Runs Oxfmt fix on a file with formatting issues and verifies
    the file content changes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_js_file_unformatted: Path to file with formatting issues.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    original = Path(temp_js_file_unformatted).read_text()

    result = oxfmt_plugin.fix([temp_js_file_unformatted], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxfmt")

    new_content = Path(temp_js_file_unformatted).read_text()
    assert_that(new_content).is_not_equal_to(original)


def test_fix_typescript_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_ts_file_unformatted: str,
) -> None:
    """Verify Oxfmt fix works with TypeScript files.

    Runs Oxfmt fix on a TypeScript file and verifies content changes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_ts_file_unformatted: Path to TypeScript file with formatting issues.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    original = Path(temp_ts_file_unformatted).read_text()

    result = oxfmt_plugin.fix([temp_ts_file_unformatted], {})

    assert_that(result).is_not_none()

    new_content = Path(temp_ts_file_unformatted).read_text()
    assert_that(new_content).is_not_equal_to(original)


def test_fix_formatted_file_unchanged(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_js_file_formatted: str,
) -> None:
    """Verify Oxfmt fix doesn't change already formatted files.

    Runs Oxfmt fix on a clean file and verifies the content stays the same.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_js_file_formatted: Path to formatted file.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    original = Path(temp_js_file_formatted).read_text()

    result = oxfmt_plugin.fix([temp_js_file_formatted], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()

    new_content = Path(temp_js_file_formatted).read_text()
    assert_that(new_content).is_equal_to(original)


def test_fix_reports_issue_counts(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_js_file_unformatted: str,
) -> None:
    """Verify Oxfmt fix reports correct issue counts.

    Runs Oxfmt fix and verifies that initial, fixed, and remaining
    issue counts are reported.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_js_file_unformatted: Path to file with formatting issues.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    result = oxfmt_plugin.fix([temp_js_file_unformatted], {})

    assert_that(result).is_not_none()
    assert_that(result.initial_issues_count).is_not_none()
    assert_that(result.fixed_issues_count).is_not_none()
    assert_that(result.remaining_issues_count).is_not_none()


# --- Tests for OxfmtPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("verbose_fix_output", True, True),
        ("verbose_fix_output", False, False),
    ],
    ids=["verbose_fix_output_true", "verbose_fix_output_false"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify OxfmtPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    oxfmt_plugin.set_options(**{option_name: option_value})
    assert_that(oxfmt_plugin.options.get(option_name)).is_equal_to(expected)


# --- Integration tests for new oxfmt options ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("config", ".oxfmtrc.json", ".oxfmtrc.json"),
        ("ignore_path", ".oxfmtignore", ".oxfmtignore"),
    ],
    ids=[
        "config",
        "ignore_path",
    ],
)
def test_set_formatting_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify OxfmtPlugin.set_options correctly sets CLI options.

    Tests that CLI options can be set and retrieved correctly.

    Note:
        Formatting options (print_width, tab_width, use_tabs, semi, single_quote)
        are only supported via config file (.oxfmtrc.json), not CLI flags.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    oxfmt_plugin.set_options(**{option_name: option_value})
    assert_that(oxfmt_plugin.options.get(option_name)).is_equal_to(expected)


def test_set_exclude_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OxfmtPlugin.set_options correctly sets exclude_patterns.

    Tests that exclude patterns can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    oxfmt_plugin = get_plugin("oxfmt")
    oxfmt_plugin.set_options(exclude_patterns=["node_modules", "dist"])
    assert_that(oxfmt_plugin.exclude_patterns).contains("node_modules")
    assert_that(oxfmt_plugin.exclude_patterns).contains("dist")


def test_config_option_accepted_by_fix(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify config option is accepted and passed to oxfmt.

    Tests that setting config path is accepted by the plugin and the fix
    command completes successfully.

    Note:
        Formatting options (print_width, tab_width, use_tabs, semi, single_quote)
        are only supported via config file (.oxfmtrc.json), not CLI flags.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    # Create a config file
    config_path = tmp_path / ".oxfmtrc.json"
    config_path.write_text('{"printWidth": 40}\n')

    # Create file with a long line
    file_path = tmp_path / "test.js"
    file_path.write_text(
        "const longLine = { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, g: 7, h: 8, i: 9 };\n",
    )

    oxfmt_plugin = get_plugin("oxfmt")
    # Set the config path
    oxfmt_plugin.set_options(config=str(config_path))

    result = oxfmt_plugin.fix([str(file_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("oxfmt")
    # Verify the option was stored correctly
    assert_that(oxfmt_plugin.options.get("config")).is_equal_to(str(config_path))
