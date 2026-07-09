"""Tests for Prettier config discovery methods."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from assertpy import assert_that

if TYPE_CHECKING:
    from pathlib import Path

    from lintro.tools.definitions.prettier import PrettierPlugin


# Tests for _find_prettier_config method


def test_find_prettier_config_not_found(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
) -> None:
    """Returns None when no config file exists.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    result = prettier_plugin._find_prettier_config(search_dir=str(tmp_path))
    assert_that(result).is_none()


def test_find_prettier_config_found_prettierrc(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
) -> None:
    """Returns path when .prettierrc exists.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / ".prettierrc").write_text("{}\n")

    result = prettier_plugin._find_prettier_config(search_dir=str(tmp_path))
    assert_that(result).is_not_none()
    assert_that(result).contains(".prettierrc")


def test_find_prettier_config_found_in_parent(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
) -> None:
    """Returns path when config exists in an ancestor directory.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / ".prettierrc").write_text("{}\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    result = prettier_plugin._find_prettier_config(search_dir=str(nested))
    assert_that(result).is_not_none()
    assert_that(result).contains(".prettierrc")


def test_find_prettier_config_skips_package_json_without_prettier(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
) -> None:
    """Skips package.json lacking a prettier key and keeps searching upward.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / ".prettierrc").write_text("{}\n")
    nested = tmp_path / "child"
    nested.mkdir()
    (nested / "package.json").write_text('{"name": "child"}\n')

    result = prettier_plugin._find_prettier_config(search_dir=str(nested))
    assert_that(result).is_not_none()
    assert_that(result).contains(".prettierrc")


def test_find_prettier_config_uses_package_json_with_prettier(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
) -> None:
    """Uses package.json when it declares a prettier key.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "package.json").write_text('{"prettier": {"printWidth": 100}}\n')

    result = prettier_plugin._find_prettier_config(search_dir=str(tmp_path))
    assert_that(result).is_not_none()
    assert_that(result).contains("package.json")


# Tests for _find_prettierignore method


def test_find_prettierignore_not_found(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
) -> None:
    """Returns None when no .prettierignore file exists.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    result = prettier_plugin._find_prettierignore(search_dir=str(tmp_path))
    assert_that(result).is_none()


def test_find_prettierignore_found(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
) -> None:
    """Returns path when .prettierignore exists.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / ".prettierignore").write_text("dist/\n")

    result = prettier_plugin._find_prettierignore(search_dir=str(tmp_path))
    assert_that(result).is_not_none()
    assert_that(result).contains(".prettierignore")


# Tests for _build_config_args with builtin defaults


def test_build_config_args_returns_args_when_builtin_defaults_exist(
    prettier_plugin: PrettierPlugin,
) -> None:
    """Should return config args when TOOL_BUILTIN_DEFAULTS has prettier entry.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
    """
    mock_config = MagicMock()
    mock_config.enforce.line_length = None
    mock_config.enforce.target_python = None
    mock_config.get_tool_defaults.return_value = {}

    with patch(
        "lintro.tools.core.config_injection._get_lintro_config",
        return_value=mock_config,
    ):
        with patch(
            "lintro.tools.core.config_injection.generate_defaults_config",
        ) as mock_gen:
            from pathlib import Path

            mock_gen.return_value = Path("/tmp/test.json")
            args = prettier_plugin._build_config_args()

    # Should have generated args since builtin defaults exist for prettier
    assert_that(args).contains("--no-config")
    assert_that(args).contains("--config")
    # Verify correct ordering: --no-config before --config <path>
    no_config_idx = args.index("--no-config")
    config_idx = args.index("--config")
    assert_that(no_config_idx).is_less_than(config_idx)
    # The path should follow --config
    assert_that(args[config_idx + 1]).is_equal_to("/tmp/test.json")
