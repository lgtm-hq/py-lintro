"""Definition, option and command-building tests for the pylint plugin."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.pylint import (
    PYLINT_DEFAULT_PRIORITY,
    PYLINT_DEFAULT_TIMEOUT,
    PylintPlugin,
)


def test_definition_metadata(pylint_plugin: PylintPlugin) -> None:
    """The definition advertises a check-only Python linter.

    Args:
        pylint_plugin: Plugin under test.
    """
    definition = pylint_plugin.definition

    assert_that(definition.name).is_equal_to("pylint")
    assert_that(definition.name).is_equal_to(ToolName.PYLINT.value)
    assert_that(definition.can_fix).is_false()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)
    assert_that(definition.file_patterns).is_equal_to(["*.py", "*.pyi"])
    # Literal values, not the production constants, so a changed default is caught.
    assert_that(definition.priority).is_equal_to(50)
    assert_that(definition.default_timeout).is_equal_to(900)
    assert_that(definition.default_options).is_equal_to(
        {"timeout": 900, "disable": None, "enable": None},
    )
    assert_that(definition.version_command).is_equal_to(["pylint", "--version"])
    # json2 is the reporter the parser reads; it first shipped in pylint 3.2.
    assert_that(definition.min_version).is_equal_to("3.2.0")
    # Every filename discovery accepts must be advertised: tool_configuration.py
    # auto-includes an unmapped tool when a native config basename exists.
    # pylint's own resolution order, dedicated rc files first.
    assert_that(definition.native_configs).is_equal_to(
        [
            "pylintrc",
            "pylintrc.toml",
            ".pylintrc",
            ".pylintrc.toml",
            "pyproject.toml",
            "setup.cfg",
            "tox.ini",
        ],
    )


def test_module_constants_match_the_definition(pylint_plugin: PylintPlugin) -> None:
    """The exported constants stay in step with the definition they feed.

    Args:
        pylint_plugin: Plugin under test.
    """
    definition = pylint_plugin.definition

    assert_that(PYLINT_DEFAULT_PRIORITY).is_equal_to(definition.priority)
    assert_that(PYLINT_DEFAULT_TIMEOUT).is_equal_to(definition.default_timeout)


def test_build_check_command_uses_json2_and_the_resolved_config(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
) -> None:
    """The command asks for json2 and pins the discovered rcfile.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
    """
    config = configured_project / "pyproject.toml"

    cmd = pylint_plugin._build_check_command(
        files=["pkg/module.py"],
        config_path=config,
    )

    assert_that(cmd[0]).ends_with("pylint")
    assert_that(cmd[1:]).is_equal_to(
        [
            "--output-format=json2",
            "--rcfile",
            str(config),
            "pkg/module.py",
        ],
    )


def test_build_check_command_without_a_config_omits_rcfile(
    pylint_plugin: PylintPlugin,
) -> None:
    """With no configuration found, pylint's own defaults apply.

    Args:
        pylint_plugin: Plugin under test.
    """
    cmd = pylint_plugin._build_check_command(files=["a.py"], config_path=None)

    assert_that(cmd).does_not_contain("--rcfile")
    assert_that(cmd[-1]).is_equal_to("a.py")


def test_build_check_command_maps_disable_and_enable_options(
    pylint_plugin: PylintPlugin,
) -> None:
    """``pylint:disable=`` / ``pylint:enable=`` become ``--disable=`` / ``--enable=``.

    Args:
        pylint_plugin: Plugin under test.
    """
    pylint_plugin.set_options(disable="all", enable="duplicate-code")

    cmd = pylint_plugin._build_check_command(files=["a.py"], config_path=None)

    # The ``=`` form is load-bearing: pylint rejects a space-separated value
    # for these two options with exit 32.
    assert_that(cmd).contains("--disable=all")
    assert_that(cmd).contains("--enable=duplicate-code")
    # The file list stays last so the options apply to it.
    assert_that(cmd[-1]).is_equal_to("a.py")


def test_build_check_command_joins_piped_message_lists(
    pylint_plugin: PylintPlugin,
) -> None:
    """A pipe-separated ``--tool-options`` list becomes pylint's comma list.

    ``--tool-options`` splits its own entries on commas, so several message
    ids are written ``pylint:disable=C0114|R0801`` and arrive as a list.
    Interpolating that list directly would hand pylint a Python repr.

    Args:
        pylint_plugin: Plugin under test.
    """
    pylint_plugin.set_options(
        disable=["all", "C0114"],
        enable=["duplicate-code", "R0801"],
    )

    cmd = pylint_plugin._build_check_command(files=["a.py"], config_path=None)

    assert_that(cmd).contains("--disable=all,C0114")
    assert_that(cmd).contains("--enable=duplicate-code,R0801")


def test_set_options_ignores_unset_values(pylint_plugin: PylintPlugin) -> None:
    """Omitted options leave the defaults alone rather than writing ``None``.

    Args:
        pylint_plugin: Plugin under test.
    """
    pylint_plugin.set_options(enable="duplicate-code")

    assert_that(pylint_plugin.options["enable"]).is_equal_to("duplicate-code")
    assert_that(pylint_plugin.options["disable"]).is_none()


def test_set_options_overrides_timeout(pylint_plugin: PylintPlugin) -> None:
    """Runtime options override the default timeout.

    Args:
        pylint_plugin: Plugin under test.
    """
    pylint_plugin.set_options(timeout=45)

    assert_that(pylint_plugin.options["timeout"]).is_equal_to(45)


def test_doc_url_points_at_the_messages_overview(pylint_plugin: PylintPlugin) -> None:
    """A message id resolves to pylint's messages overview page.

    Args:
        pylint_plugin: Plugin under test.
    """
    url = pylint_plugin.doc_url("R0801")

    assert_that(url).is_equal_to(DocUrlTemplate.PYLINT)
    # Assert the user-facing path too: enum equality alone would not catch a
    # value edited into a URL that 404s.
    assert_that(url).contains("pylint.readthedocs.io", "messages_overview")
    # The template takes no code placeholder; a stray one would ship literally.
    assert_that(url).does_not_contain("{code}")


def test_doc_url_empty_code_returns_none(pylint_plugin: PylintPlugin) -> None:
    """An empty message id has no documentation URL.

    Args:
        pylint_plugin: Plugin under test.
    """
    assert_that(pylint_plugin.doc_url("")).is_none()


def test_fix_is_not_supported(pylint_plugin: PylintPlugin) -> None:
    """Pylint offers no fix mode.

    Args:
        pylint_plugin: Plugin under test.
    """
    with pytest.raises(NotImplementedError):
        pylint_plugin.fix(["."], {})
