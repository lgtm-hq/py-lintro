"""Integration tests for rustfmt tool definition.

These tests require rustfmt and cargo to be installed and available in PATH.
They verify the RustfmtPlugin definition, check command, fix command, and
set_options method.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that
from packaging.version import Version

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


def _get_rustfmt_version() -> Version | None:
    """Get the installed rustfmt version.

    Returns:
        Version object or None if not installed or version cannot be determined.
    """
    if shutil.which("rustfmt") is None:
        return None
    try:
        result = subprocess.run(
            ["rustfmt", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Output format: "rustfmt <version>-<channel> (<commit-hash> <date>)"
        match = re.search(r"(\d+\.\d+\.\d+)", result.stdout)
        if match:
            return Version(match.group(1))
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


_RUSTFMT_MIN_VERSION = Version("1.8.0")
_installed_version = _get_rustfmt_version()

# Skip all tests if rustfmt is not installed or version is below minimum
pytestmark = pytest.mark.skipif(
    shutil.which("rustfmt") is None
    or shutil.which("cargo") is None
    or _installed_version is None
    or _installed_version < _RUSTFMT_MIN_VERSION,
    reason=f"rustfmt >= {_RUSTFMT_MIN_VERSION} or cargo not installed "
    f"(found: {_installed_version})",
)


@pytest.fixture
def temp_rust_project_with_issues(tmp_path: Path) -> str:
    """Create a temporary Rust project with formatting issues.

    Creates a Cargo project containing Rust code with formatting issues that
    rustfmt should detect, including:
    - Missing spaces around braces
    - Inconsistent formatting

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the project directory as a string.
    """
    project_dir = tmp_path / "rust_project"
    project_dir.mkdir()

    # Create Cargo.toml
    (project_dir / "Cargo.toml").write_text(
        """\
[package]
name = "test_project"
version = "0.1.0"
edition = "2021"
""",
    )

    # Create src directory with poorly formatted code
    src_dir = project_dir / "src"
    src_dir.mkdir()
    (src_dir / "main.rs").write_text(
        """\
fn main(){let x=1;let y=2;println!("{} {}",x,y);}
""",
    )

    return str(project_dir)


@pytest.fixture
def temp_rust_project_clean(tmp_path: Path) -> str:
    """Create a temporary Rust project with no formatting issues.

    Creates a Cargo project containing properly formatted Rust code that
    should pass rustfmt checking without issues.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the project directory as a string.
    """
    project_dir = tmp_path / "rust_project_clean"
    project_dir.mkdir()

    # Create Cargo.toml
    (project_dir / "Cargo.toml").write_text(
        """\
[package]
name = "test_project"
version = "0.1.0"
edition = "2021"
""",
    )

    # Create src directory with well-formatted code
    src_dir = project_dir / "src"
    src_dir.mkdir()
    (src_dir / "main.rs").write_text(
        """\
fn main() {
    let x = 1;
    let y = 2;
    println!("{} {}", x, y);
}
""",
    )

    return str(project_dir)


@pytest.fixture
def temp_rust_project_complex_issues(tmp_path: Path) -> str:
    """Create a temporary Rust project with multiple formatting issues.

    Creates a Cargo project containing code with various formatting issues
    that rustfmt should fix, including:
    - Missing spaces around operators
    - Incorrect indentation
    - Missing newlines

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the project directory as a string.
    """
    project_dir = tmp_path / "rust_project_complex"
    project_dir.mkdir()

    # Create Cargo.toml
    (project_dir / "Cargo.toml").write_text(
        """\
[package]
name = "test_project"
version = "0.1.0"
edition = "2021"
""",
    )

    # Create src directory with multiple formatting issues
    src_dir = project_dir / "src"
    src_dir.mkdir()
    (src_dir / "main.rs").write_text(
        """\
fn main(){if true{println!("yes");}else{println!("no");}}
fn helper(x:i32,y:i32)->i32{x+y}
""",
    )

    return str(project_dir)


# --- Tests for RustfmtPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "rustfmt"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify RustfmtPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    assert_that(getattr(rustfmt_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify RustfmtPlugin definition includes Rust file patterns.

    Tests that the plugin is configured to handle Rust files (*.rs).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    assert_that(rustfmt_plugin.definition.file_patterns).contains("*.rs")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify RustfmtPlugin definition has a version command.

    Tests that the plugin exposes a version command for checking
    the installed rustfmt version.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    assert_that(rustfmt_plugin.definition.version_command).is_not_none()


# --- Integration tests for rustfmt check command ---


def test_check_project_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_rust_project_with_issues: str,
) -> None:
    """Verify rustfmt check detects formatting issues in problematic projects.

    Runs rustfmt on a project containing formatting issues and verifies that
    issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_rust_project_with_issues: Path to project with formatting issues.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    result = rustfmt_plugin.check([temp_rust_project_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("rustfmt")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_project(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_rust_project_clean: str,
) -> None:
    """Verify rustfmt check passes on clean projects.

    Runs rustfmt on a clean project and verifies no issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_rust_project_clean: Path to clean project.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    result = rustfmt_plugin.check([temp_rust_project_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("rustfmt")
    assert_that(result.success).is_true()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify rustfmt check handles empty directories gracefully.

    Runs rustfmt on an empty directory and verifies a result is returned
    with zero issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    result = rustfmt_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_no_cargo_toml(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify rustfmt check handles projects without Cargo.toml.

    Runs rustfmt on a directory with Rust files but no Cargo.toml.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    # Create a Rust file without Cargo.toml
    test_file = tmp_path / "main.rs"
    test_file.write_text("fn main() {}\n")

    rustfmt_plugin = get_plugin("rustfmt")
    result = rustfmt_plugin.check([str(test_file)], {})

    assert_that(result).is_not_none()
    assert_that(result.output).contains("No Cargo.toml found")


# --- Integration tests for rustfmt fix command ---


def test_fix_formats_project(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_rust_project_with_issues: str,
) -> None:
    """Verify rustfmt fix reformats projects with formatting issues.

    Runs rustfmt fix on a project with formatting issues and verifies
    the files are reformatted.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_rust_project_with_issues: Path to project with formatting issues.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    project_path = Path(temp_rust_project_with_issues)
    main_rs = project_path / "src" / "main.rs"
    original = main_rs.read_text()

    result = rustfmt_plugin.fix([temp_rust_project_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("rustfmt")

    new_content = main_rs.read_text()
    assert_that(new_content).is_not_equal_to(original)


def test_fix_complex_project(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_rust_project_complex_issues: str,
) -> None:
    """Verify rustfmt fix handles complex formatting issues.

    Runs rustfmt fix on a project with multiple formatting issues and verifies
    fixes are applied.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_rust_project_complex_issues: Path to project with complex issues.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    project_path = Path(temp_rust_project_complex_issues)
    main_rs = project_path / "src" / "main.rs"
    original = main_rs.read_text()

    result = rustfmt_plugin.fix([temp_rust_project_complex_issues], {})

    assert_that(result).is_not_none()

    new_content = main_rs.read_text()
    assert_that(new_content).is_not_equal_to(original)


def test_fix_clean_project_unchanged(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_rust_project_clean: str,
) -> None:
    """Verify rustfmt fix doesn't change already formatted projects.

    Runs rustfmt fix on a clean project and verifies the content stays the same.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_rust_project_clean: Path to clean project.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    project_path = Path(temp_rust_project_clean)
    main_rs = project_path / "src" / "main.rs"
    original = main_rs.read_text()

    result = rustfmt_plugin.fix([temp_rust_project_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()

    new_content = main_rs.read_text()
    assert_that(new_content).is_equal_to(original)


# --- Tests for RustfmtPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("timeout", 30, 30),
        ("timeout", 60, 60),
        ("timeout", 120, 120),
    ],
    ids=[
        "timeout_30",
        "timeout_60",
        "timeout_120",
    ],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify RustfmtPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    rustfmt_plugin.set_options(**{option_name: option_value})
    assert_that(rustfmt_plugin.options.get(option_name)).is_equal_to(expected)


def test_invalid_timeout(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify RustfmtPlugin.set_options rejects invalid timeout values.

    Tests that invalid timeout values raise ValueError.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    rustfmt_plugin = get_plugin("rustfmt")
    with pytest.raises(ValueError, match="must be positive"):
        rustfmt_plugin.set_options(timeout=-1)
