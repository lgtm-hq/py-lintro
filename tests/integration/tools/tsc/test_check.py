"""Integration tests for TypeScript Compiler (tsc) tool definition.

These tests require tsc (typescript) to be installed and available in PATH.
They verify the TscPlugin definition, check command, and set_options method.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration.tools.tsc.conftest import tsc_is_available

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if tsc is not installed or not working
pytestmark = pytest.mark.skipif(
    not tsc_is_available(),
    reason="tsc not installed or not working",
)


# --- Tests for TscPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "tsc"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify TscPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    tsc_plugin = get_plugin("tsc")
    assert_that(getattr(tsc_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(get_plugin: Callable[[str], BaseToolPlugin]) -> None:
    """Verify TscPlugin definition includes TypeScript and JavaScript patterns.

    Tests that the plugin is configured to handle TypeScript files and
    JSDoc-typed JavaScript (*.js / *.mjs / *.cjs / *.jsx) for checkJs.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    tsc_plugin = get_plugin("tsc")
    assert_that(tsc_plugin.definition.file_patterns).contains("*.ts")
    assert_that(tsc_plugin.definition.file_patterns).contains("*.tsx")
    assert_that(tsc_plugin.definition.file_patterns).contains("*.js")
    assert_that(tsc_plugin.definition.file_patterns).contains("*.mjs")
    assert_that(tsc_plugin.definition.file_patterns).contains("*.cjs")
    assert_that(tsc_plugin.definition.file_patterns).contains("*.jsx")


# --- Integration tests for tsc check command ---


def test_check_file_with_type_errors(
    get_plugin: Callable[[str], BaseToolPlugin],
    tsc_violation_file: str,
) -> None:
    """Verify tsc check detects type errors in problematic files.

    Runs tsc on a file containing deliberate type violations and verifies
    that issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tsc_violation_file: Path to file with type errors from test_samples.
    """
    tsc_plugin = get_plugin("tsc")
    result = tsc_plugin.check([tsc_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("tsc")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_type_correct_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    tsc_clean_file: str,
) -> None:
    """Verify tsc check passes on type-correct files.

    Runs tsc on a properly typed file and verifies no issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tsc_clean_file: Path to file with correct types from test_samples.
    """
    tsc_plugin = get_plugin("tsc")
    result = tsc_plugin.check([tsc_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("tsc")
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify tsc check handles empty directories gracefully.

    Runs tsc on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    tsc_plugin = get_plugin("tsc")
    result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()


# --- Tests for TscPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("project", "tsconfig.json", "tsconfig.json"),
        ("strict", True, True),
        ("skip_lib_check", True, True),
        ("use_project_files", True, True),
        ("use_project_files", False, False),
    ],
    ids=[
        "project",
        "strict",
        "skip_lib_check",
        "use_project_files_true",
        "use_project_files_false",
    ],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify TscPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    tsc_plugin = get_plugin("tsc")
    tsc_plugin.set_options(**{option_name: option_value})
    assert_that(tsc_plugin.options.get(option_name)).is_equal_to(expected)


# --- Tests for file targeting with tsconfig.json ---


def test_file_targeting_with_tsconfig(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify tsc respects file targeting even when tsconfig.json exists.

    Creates a project with tsconfig.json and multiple files, then verifies
    that only the specified file is checked (not all files in the project).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    # Create tsconfig.json
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text('{"compilerOptions": {"strict": true}}')

    # Create two TypeScript files - one clean, one with errors
    clean_file = tmp_path / "clean.ts"
    clean_file.write_text("const x: number = 42;\nexport { x };\n")

    error_file = tmp_path / "error.ts"
    error_file.write_text("const y: number = 'string';\nexport { y };\n")

    tsc_plugin = get_plugin("tsc")

    # Check only the clean file - should pass
    result = tsc_plugin.check([str(clean_file)], {})
    assert_that(result.issues_count).is_equal_to(0)

    # Check only the error file - should find issues
    result = tsc_plugin.check([str(error_file)], {})
    assert_that(result.issues_count).is_greater_than(0)


def test_use_project_files_checks_all_files(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify use_project_files=True uses tsconfig.json file selection.

    When use_project_files is True, tsc should check all files defined
    in tsconfig.json, not just the files passed to check().

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    # Create tsconfig.json that includes all .ts files
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(
        '{"compilerOptions": {"strict": true}, "include": ["*.ts"]}',
    )

    # Create a clean file and an error file
    clean_file = tmp_path / "clean.ts"
    clean_file.write_text("const x: number = 42;\nexport { x };\n")

    error_file = tmp_path / "error.ts"
    error_file.write_text("const y: number = 'string';\nexport { y };\n")

    tsc_plugin = get_plugin("tsc")

    # With use_project_files=True, checking just clean.ts should still find
    # errors because tsc checks all files in tsconfig.json
    result = tsc_plugin.check(
        [str(clean_file)],
        {"use_project_files": True},
    )

    # Should find the error in error.ts even though we only passed clean.ts
    assert_that(result.issues_count).is_greater_than(0)


# --- Tests for checkJs / JSDoc JavaScript (issue #1185) ---


def test_check_js_with_checkjs_finds_type_errors(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify tsc type-checks JSDoc JavaScript when checkJs is enabled.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions": {"strict": true, "checkJs": true, "allowJs": true, '
        '"noEmit": true}, "include": ["*.js"]}',
    )
    js_file = tmp_path / "bad.js"
    js_file.write_text(
        "/** @type {number} */\nconst x = 'not-a-number';\nexport { x };\n",
    )

    tsc_plugin = get_plugin("tsc")
    result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_js_without_checkjs_skips(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify JS-only projects without checkJs do not activate tsc findings.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions": {"strict": true, "allowJs": true, "noEmit": true}, '
        '"include": ["*.js"]}',
    )
    js_file = tmp_path / "bad.js"
    js_file.write_text(
        "/** @type {number} */\nconst x = 'not-a-number';\nexport { x };\n",
    )

    tsc_plugin = get_plugin("tsc")
    result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.skipped).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.skip_reason).contains("checkJs")


def test_check_mixed_ts_js_with_checkjs(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify mixed TS+JS projects with checkJs report JSDoc errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions": {"strict": true, "checkJs": true, "allowJs": true, '
        '"noEmit": true}, "include": ["*.ts", "*.js"]}',
    )
    (tmp_path / "ok.ts").write_text("export const n: number = 1;\n")
    (tmp_path / "bad.js").write_text(
        "/** @type {number} */\nconst x = 'not-a-number';\nexport { x };\n",
    )

    tsc_plugin = get_plugin("tsc")
    result = tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_greater_than(0)
