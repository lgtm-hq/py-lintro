"""Integration tests for Prettier tool definition.

These tests require prettier to be installed (npm install -g prettier).
They verify the PrettierPlugin definition, check command, fix command, and
set_options method.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if prettier is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("prettier") is None,
    reason="prettier not installed",
)


@pytest.fixture
def temp_json_file_unformatted(tmp_path: Path) -> str:
    """Create a temporary JSON file with formatting issues.

    Creates a file containing a single-line JSON object that Prettier
    should format with proper indentation and newlines.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "unformatted.json"
    file_path.write_text('{"name":"test","version":"1.0.0","dependencies":{}}')
    return str(file_path)


# --- Tests for PrettierPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "prettier"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify PrettierPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    prettier_plugin = get_plugin("prettier")
    assert_that(getattr(prettier_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(get_plugin: Callable[[str], BaseToolPlugin]) -> None:
    """Verify PrettierPlugin definition includes non-JS/TS file patterns.

    Tests that the plugin is configured to handle CSS, HTML, JSON, YAML, Markdown,
    GraphQL, and Astro files. JS/TS files are handled by oxfmt for better performance.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    prettier_plugin = get_plugin("prettier")
    patterns = prettier_plugin.definition.file_patterns
    # Prettier handles non-JS/TS files (JS/TS delegated to oxfmt)
    has_expected_patterns = "*.css" in patterns and "*.json" in patterns
    assert_that(has_expected_patterns).is_true()
    # Verify Astro pattern is included
    assert_that("*.astro" in patterns).is_true()
    # Verify JS/TS patterns are NOT in prettier (handled by oxfmt)
    assert_that("*.js" in patterns or "*.ts" in patterns).is_false()


# --- Integration tests for prettier check command ---


@pytest.fixture
def temp_json_file_formatted(tmp_path: Path) -> str:
    """Create a temporary JSON file that is already formatted.

    Creates a file containing properly formatted JSON that
    Prettier should leave unchanged.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "formatted.json"
    file_path.write_text(
        """\
{
  "name": "test",
  "version": "1.0.0",
  "dependencies": {}
}
""",
    )
    return str(file_path)


@pytest.mark.parametrize(
    ("file_fixture", "expect_issues"),
    [
        ("temp_json_file_unformatted", True),
        ("temp_json_file_formatted", False),
    ],
    ids=["unformatted_json", "formatted_json"],
)
def test_check_json_file_formatting_state(
    get_plugin: Callable[[str], BaseToolPlugin],
    file_fixture: str,
    expect_issues: bool,
    request: pytest.FixtureRequest,
) -> None:
    """Verify Prettier check correctly detects JSON file formatting state.

    Runs Prettier in check mode on files with different formatting states
    and verifies the expected issue count. Note: JS/TS files are handled
    by oxfmt, so we test with JSON files here.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        file_fixture: Name of the fixture providing the file path.
        expect_issues: Whether issues are expected (True for unformatted).
        request: Pytest request fixture for dynamic fixture access.
    """
    file_path = request.getfixturevalue(file_fixture)
    prettier_plugin = get_plugin("prettier")
    result = prettier_plugin.check([file_path], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("prettier")
    if expect_issues:
        assert_that(result.issues_count).is_greater_than(0)
    else:
        assert_that(result.issues_count).is_equal_to(0)


# --- Integration tests for prettier fix command ---


def test_fix_formats_json_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_json_file_unformatted: str,
) -> None:
    """Verify Prettier fix reformats JSON files.

    Runs Prettier fix on a JSON file and verifies the file is
    formatted with proper indentation.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_json_file_unformatted: Path to JSON file with formatting issues.
    """
    prettier_plugin = get_plugin("prettier")
    original = Path(temp_json_file_unformatted).read_text()

    result = prettier_plugin.fix([temp_json_file_unformatted], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()

    new_content = Path(temp_json_file_unformatted).read_text()
    assert_that(new_content).is_not_equal_to(original)


# --- Tests for PrettierPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("verbose_fix_output", True, True),
        ("line_length", 120, 120),
    ],
    ids=["verbose_fix_output", "line_length"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify PrettierPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.
    Note: Formatting options like tab_width, use_tabs, single_quote are
    configured via .prettierrc config file, not via set_options.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    prettier_plugin = get_plugin("prettier")
    prettier_plugin.set_options(**{option_name: option_value})
    assert_that(prettier_plugin.options.get(option_name)).is_equal_to(expected)


# --- Integration tests for Astro file formatting ---

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def astro_project_dir(tmp_path: Path) -> Path:
    """Create a temporary project directory with prettier-plugin-astro configured.

    Sets up a .prettierrc that loads prettier-plugin-astro so Prettier
    can parse and format .astro files. Symlinks node_modules from the
    project root so the plugin can be resolved.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the temporary project directory.
    """
    # Symlink node_modules so prettier can resolve the astro plugin
    project_node_modules = PROJECT_ROOT / "node_modules"
    if not project_node_modules.exists():
        pytest.skip("node_modules not found; run bun install first")
    try:
        (tmp_path / "node_modules").symlink_to(project_node_modules)
    except OSError as exc:
        pytest.skip(f"Cannot create symlink: {exc}")

    prettierrc = {"plugins": ["prettier-plugin-astro"]}
    (tmp_path / ".prettierrc").write_text(json.dumps(prettierrc))
    return tmp_path


@pytest.fixture
def temp_astro_file_unformatted(astro_project_dir: Path) -> str:
    """Create a temporary Astro file with formatting issues.

    Creates a file with bad indentation, missing semicolons, and
    inconsistent quotes that Prettier should reformat.

    Args:
        astro_project_dir: Temporary project with .prettierrc configured.

    Returns:
        Path to the created file as a string.
    """
    file_path = astro_project_dir / "unformatted.astro"
    file_path.write_text(
        '---\nconst greeting = "Hello"\n'
        'const   items = ["apple","banana","cherry"]\n'
        "---\n\n<html>\n<head>\n<title>Test</title>\n"
        "</head>\n<body>\n    <h1>{greeting}</h1>\n"
        "<ul>\n{items.map((item) => (\n<li>{item}</li>\n"
        "))}\n</ul>\n</body>\n</html>\n",
    )
    return str(file_path)


@pytest.fixture
def temp_astro_file_formatted(astro_project_dir: Path) -> str:
    """Create a temporary Astro file that is already formatted.

    Creates a file that conforms to Prettier's default formatting
    with prettier-plugin-astro.

    Args:
        astro_project_dir: Temporary project with .prettierrc configured.

    Returns:
        Path to the created file as a string.
    """
    file_path = astro_project_dir / "formatted.astro"
    file_path.write_text(
        '---\nconst greeting = "Hello";\n'
        'const items = ["apple", "banana", "cherry"];\n'
        "---\n\n<html>\n  <head>\n    <title>Test</title>\n"
        "  </head>\n  <body>\n    <h1>{greeting}</h1>\n"
        "    <ul>\n      {items.map((item) => <li>{item}</li>)}\n"
        "    </ul>\n  </body>\n</html>\n",
    )
    return str(file_path)


@pytest.mark.parametrize(
    ("file_fixture", "expect_issues"),
    [
        ("temp_astro_file_unformatted", True),
        ("temp_astro_file_formatted", False),
    ],
    ids=["unformatted_astro", "formatted_astro"],
)
def test_check_astro_file_formatting_state(
    get_plugin: Callable[[str], BaseToolPlugin],
    file_fixture: str,
    expect_issues: bool,
    request: pytest.FixtureRequest,
) -> None:
    """Verify Prettier check correctly detects Astro file formatting state.

    Runs Prettier in check mode on .astro files with different formatting
    states and verifies the expected issue count. Requires prettier-plugin-astro
    to be installed and configured via .prettierrc.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        file_fixture: Name of the fixture providing the file path.
        expect_issues: Whether issues are expected (True for unformatted).
        request: Pytest request fixture for dynamic fixture access.
    """
    file_path = request.getfixturevalue(file_fixture)
    prettier_plugin = get_plugin("prettier")
    result = prettier_plugin.check([file_path], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("prettier")
    if expect_issues:
        assert_that(result.issues_count).is_greater_than(0)
    else:
        assert_that(result.issues_count).is_equal_to(0)


def test_fix_formats_astro_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_astro_file_unformatted: str,
) -> None:
    """Verify Prettier fix reformats Astro files.

    Runs Prettier fix on an Astro file and verifies the file is
    reformatted with proper indentation and consistent style.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_astro_file_unformatted: Path to Astro file with formatting issues.
    """
    prettier_plugin = get_plugin("prettier")
    original = Path(temp_astro_file_unformatted).read_text()

    result = prettier_plugin.fix([temp_astro_file_unformatted], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()

    new_content = Path(temp_astro_file_unformatted).read_text()
    assert_that(new_content).is_not_equal_to(original)
