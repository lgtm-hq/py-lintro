"""Tests for BufPlugin options and command building."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.buf import BUF_DEFAULT_TIMEOUT, BufPlugin


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", BUF_DEFAULT_TIMEOUT),
        ("config", None),
        ("disable_symlinks", None),
    ],
    ids=["timeout_default", "config_none", "disable_symlinks_none"],
)
def test_default_options(
    buf_plugin: BufPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options match the definition.

    Args:
        buf_plugin: The plugin under test.
        option_name: The option key to inspect.
        expected_value: The expected default value.
    """
    assert_that(buf_plugin.options.get(option_name)).is_equal_to(expected_value)


def test_definition_metadata(buf_plugin: BufPlugin) -> None:
    """The definition advertises linter+formatter over *.proto with fix support.

    Args:
        buf_plugin: The plugin under test.
    """
    definition = buf_plugin.definition
    assert_that(definition.name).is_equal_to("buf")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.file_patterns).contains("*.proto")
    assert_that(definition.native_configs).contains("buf.yaml")
    assert_that(bool(definition.tool_type & ToolType.LINTER)).is_true()
    assert_that(bool(definition.tool_type & ToolType.FORMATTER)).is_true()


def test_set_options_valid(buf_plugin: BufPlugin) -> None:
    """Valid options are stored on the plugin.

    Args:
        buf_plugin: The plugin under test.
    """
    buf_plugin.set_options(config="buf.yaml", disable_symlinks=True)

    assert_that(buf_plugin.options.get("config")).is_equal_to("buf.yaml")
    assert_that(buf_plugin.options.get("disable_symlinks")).is_true()


@pytest.mark.parametrize(
    ("kwargs"),
    [
        {"config": 123},
        {"disable_symlinks": "yes"},
    ],
    ids=["config_not_str", "disable_symlinks_not_bool"],
)
def test_set_options_invalid_raises(
    buf_plugin: BufPlugin,
    kwargs: dict[str, object],
) -> None:
    """Invalid option types raise ValueError.

    Args:
        buf_plugin: The plugin under test.
        kwargs: The invalid keyword arguments.
    """
    assert_that(buf_plugin.set_options).raises(ValueError).when_called_with(**kwargs)


def test_build_common_args_includes_paths(buf_plugin: BufPlugin) -> None:
    """Common args use a '.' input and one --path flag per file.

    Args:
        buf_plugin: The plugin under test.
    """
    args = buf_plugin._build_common_args(["a.proto", "sub/b.proto"])

    assert_that(args[0]).is_equal_to(".")
    assert_that(args).contains("--path", "a.proto", "sub/b.proto")


def test_build_common_args_honors_config_and_symlinks(buf_plugin: BufPlugin) -> None:
    """Config and disable-symlinks options flow into the args.

    Args:
        buf_plugin: The plugin under test.
    """
    buf_plugin.set_options(config="custom.yaml", disable_symlinks=True)
    args = buf_plugin._build_common_args(["a.proto"])

    assert_that(args).contains("--config", "custom.yaml", "--disable-symlinks")
