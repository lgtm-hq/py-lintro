"""Unit tests for mypy plugin."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.tools.definitions.mypy import (
    MYPY_DEFAULT_EXCLUDE_PATTERNS,
    _regex_to_glob,
    _split_config_values,
)

if TYPE_CHECKING:
    from lintro.tools.definitions.mypy import MypyPlugin


# Tests for _split_config_values helper


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ("a,b,c", ["a", "b", "c"]),
        ("a\nb\nc", ["a", "b", "c"]),
        ("a,b\nc,d", ["a", "b", "c", "d"]),
        ("  a  ,  b  ", ["a", "b"]),
        ("a,,b,", ["a", "b"]),
    ],
    ids=[
        "comma_separated",
        "newline_separated",
        "mixed_separators",
        "strips_whitespace",
        "filters_empty",
    ],
)
def test_split_config_values(input_value: str, expected: list[str]) -> None:
    """Split config values correctly.

    Args:
        input_value: The input string to split.
        expected: The expected list of split values.
    """
    result = _split_config_values(input_value)
    assert_that(result).is_equal_to(expected)


# Tests for _regex_to_glob helper


@pytest.mark.parametrize(
    ("input_pattern", "expected"),
    [
        ("^test$", "test"),
        ("test.*", "test*"),
        ("build/", "build/**"),
        ("^.*/test_samples/.*$", "*/test_samples/*"),
    ],
    ids=[
        "strips_anchors",
        "converts_wildcard",
        "adds_glob_for_directory",
        "complex_pattern",
    ],
)
def test_regex_to_glob(input_pattern: str, expected: str) -> None:
    """Convert regex patterns to glob patterns.

    Args:
        input_pattern: The regex pattern to convert.
        expected: The expected glob pattern.
    """
    result = _regex_to_glob(input_pattern)
    assert_that(result).is_equal_to(expected)


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("strict", True),
        ("ignore_missing_imports", False),
        ("python_version", "3.10"),
        ("config_file", "mypy.ini"),
        ("cache_dir", ".mypy_cache"),
    ],
    ids=[
        "strict",
        "ignore_missing_imports",
        "python_version",
        "config_file",
        "cache_dir",
    ],
)
def test_set_options_valid(
    mypy_plugin: MypyPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        option_name: The name of the option to set.
        option_value: The value to set for the option.
    """
    mypy_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(mypy_plugin.options.get(option_name)).is_equal_to(option_value)


# Tests for MypyPlugin.set_options method - invalid types


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("strict", "yes", "strict must be a boolean"),
        ("ignore_missing_imports", "yes", "ignore_missing_imports must be a boolean"),
        ("python_version", 310, "python_version must be a string"),
        ("config_file", 123, "config_file must be a string"),
        ("cache_dir", 123, "cache_dir must be a string"),
    ],
    ids=[
        "invalid_strict_type",
        "invalid_ignore_missing_imports_type",
        "invalid_python_version_type",
        "invalid_config_file_type",
        "invalid_cache_dir_type",
    ],
)
def test_set_options_invalid_type(
    mypy_plugin: MypyPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        option_name: The name of the option to test.
        invalid_value: An invalid value for the option.
        error_match: The expected error message pattern.
    """
    with pytest.raises(ValueError, match=error_match):
        mypy_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


# Tests for MypyPlugin._build_effective_excludes method


def test_build_effective_excludes_includes_defaults(mypy_plugin: MypyPlugin) -> None:
    """Include default exclude patterns.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
    """
    result = mypy_plugin._build_effective_excludes(None)

    for pattern in MYPY_DEFAULT_EXCLUDE_PATTERNS:
        assert_that(pattern in result).is_true()


def test_build_effective_excludes_adds_configured(mypy_plugin: MypyPlugin) -> None:
    """Add configured exclude patterns.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
    """
    result = mypy_plugin._build_effective_excludes("custom_dir/")

    assert_that("custom_dir/**" in result).is_true()


def test_build_effective_excludes_handles_list(mypy_plugin: MypyPlugin) -> None:
    """Handle list of exclude patterns.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
    """
    result = mypy_plugin._build_effective_excludes(["dir1/", "dir2/"])

    assert_that("dir1/**" in result).is_true()
    assert_that("dir2/**" in result).is_true()


def test_build_effective_excludes_converts_regex(mypy_plugin: MypyPlugin) -> None:
    """Convert regex patterns to globs.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
    """
    result = mypy_plugin._build_effective_excludes(["^tests/.*$"])

    assert_that("tests/*" in result).is_true()


# Tests for MypyPlugin._build_command strict/missing-import flag handling.
#
# Regression coverage for #1081: when a scanned project's runtime deps are not
# installed, third-party libraries resolve to ``Any``. Under ``--strict`` this
# makes ``disallow_subclassing_any``/``disallow_untyped_decorators`` fire as
# false positives, so lintro counters them with ``--allow-*`` flags whenever
# missing imports are ignored.


def test_build_command_strict_adds_allow_any_flags(mypy_plugin: MypyPlugin) -> None:
    """Emit --allow-* flags when strict and missing imports are ignored.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
    """
    mypy_plugin.set_options(strict=True, ignore_missing_imports=True)

    cmd = mypy_plugin._build_command(files=["a.py"])

    assert_that(cmd).contains(
        "--strict",
        "--allow-subclassing-any",
        "--allow-untyped-decorators",
        "--ignore-missing-imports",
    )
    # The allow-flags must follow --strict so mypy (left-to-right) honours them.
    assert_that(cmd.index("--allow-subclassing-any")).is_greater_than(
        cmd.index("--strict"),
    )
    assert_that(cmd.index("--allow-untyped-decorators")).is_greater_than(
        cmd.index("--strict"),
    )


def test_build_command_strict_without_ignore_keeps_full_strict(
    mypy_plugin: MypyPlugin,
) -> None:
    """Keep full strict behavior when missing imports are not ignored.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
    """
    mypy_plugin.set_options(strict=True, ignore_missing_imports=False)

    cmd = mypy_plugin._build_command(files=["a.py"])

    assert_that(cmd).contains("--strict")
    assert_that(cmd).does_not_contain(
        "--allow-subclassing-any",
        "--allow-untyped-decorators",
        "--ignore-missing-imports",
    )


def test_build_command_non_strict_omits_allow_any_flags(
    mypy_plugin: MypyPlugin,
) -> None:
    """Omit --allow-* flags when strict mode is disabled.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
    """
    mypy_plugin.set_options(strict=False, ignore_missing_imports=True)

    cmd = mypy_plugin._build_command(files=["a.py"])

    assert_that(cmd).does_not_contain(
        "--strict",
        "--allow-subclassing-any",
        "--allow-untyped-decorators",
    )
    assert_that(cmd).contains("--ignore-missing-imports")


# Tests for MypyPlugin.check working-directory handling.
#
# Regression coverage for #492: mypy must run from the project root (where the
# config file lives), passing file paths relative to that root, so Python
# package/module resolution works. Running from the file's parent directory
# resolved imports to ``Any`` and mis-flagged ``type: ignore`` comments.


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal Python package tree with a mypy config.

    Args:
        tmp_path: Temporary directory provided by pytest.

    Returns:
        Path: The project root containing ``pyproject.toml`` and a nested
        ``pkg/sub/mod.py`` source file.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\npython_version = "3.11"\n',
        encoding="utf-8",
    )
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "mod.py").write_text("x: int = 1\n", encoding="utf-8")
    return tmp_path


def test_check_runs_from_project_root_for_single_file(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run mypy from the project root with a root-relative path (#492).

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
    """
    project_root = _make_project(tmp_path)
    monkeypatch.chdir(project_root)

    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str],
        timeout: float,
        cwd: str | None = None,
        **_: object,
    ) -> tuple[bool, str]:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return True, ""

    monkeypatch.setattr(mypy_plugin, "_run_subprocess", fake_run_subprocess)
    mypy_plugin.set_options()

    result = mypy_plugin.check([os.path.join("pkg", "sub", "mod.py")], {})

    assert_that(result.success).is_true()
    assert_that(captured["cwd"]).is_equal_to(str(project_root))
    # The target path is relative to the project root, not the file's basename.
    assert_that(captured["cmd"]).contains(os.path.join("pkg", "sub", "mod.py"))
    assert_that(captured["cmd"]).does_not_contain("mod.py")


def test_check_target_is_relative_to_root_not_basename(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass a full root-relative path so module resolution works (#492).

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
    """
    project_root = _make_project(tmp_path)
    monkeypatch.chdir(project_root)

    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str],
        timeout: float,
        cwd: str | None = None,
        **_: object,
    ) -> tuple[bool, str]:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return True, ""

    monkeypatch.setattr(mypy_plugin, "_run_subprocess", fake_run_subprocess)
    mypy_plugin.set_options()

    mypy_plugin.check([os.path.join("pkg", "sub", "mod.py")], {})

    target = captured["cmd"][-1]  # type: ignore[index]
    assert_that(target).is_equal_to(os.path.join("pkg", "sub", "mod.py"))
    # A path relative to the file's own directory would collapse to the basename.
    assert_that(str(target)).contains(os.sep)


def test_check_directory_target_runs_from_project_root(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run mypy from the project root when a directory is passed (#492).

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Pytest monkeypatch fixture.
    """
    project_root = _make_project(tmp_path)
    monkeypatch.chdir(project_root)

    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str],
        timeout: float,
        cwd: str | None = None,
        **_: object,
    ) -> tuple[bool, str]:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return True, ""

    monkeypatch.setattr(mypy_plugin, "_run_subprocess", fake_run_subprocess)
    mypy_plugin.set_options()

    result = mypy_plugin.check(["pkg"], {})

    assert_that(result.success).is_true()
    assert_that(captured["cwd"]).is_equal_to(str(project_root))
