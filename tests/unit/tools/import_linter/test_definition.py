"""Definition and option tests for the import-linter plugin."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.import_linter import (
    IMPORT_LINTER_DEFAULT_PRIORITY,
    IMPORT_LINTER_DEFAULT_TIMEOUT,
    ImportLinterPlugin,
)


def test_definition_metadata(import_linter_plugin: ImportLinterPlugin) -> None:
    """The definition advertises a check-only Python linter.

    Args:
        import_linter_plugin: Plugin under test.
    """
    definition = import_linter_plugin.definition

    assert_that(definition.name).is_equal_to("import-linter")
    assert_that(definition.can_fix).is_false()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)
    assert_that(definition.file_patterns).is_equal_to(["*.py"])
    assert_that(definition.priority).is_equal_to(IMPORT_LINTER_DEFAULT_PRIORITY)
    assert_that(definition.default_timeout).is_equal_to(IMPORT_LINTER_DEFAULT_TIMEOUT)
    assert_that(definition.version_command).is_equal_to(["lint-imports", "--version"])
    assert_that(definition.native_configs).contains("pyproject.toml")


def test_build_command_uses_resolved_config(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
) -> None:
    """The command pins the config file and disables the logo and cache.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import contracts.
    """
    config = project_with_contracts / "pyproject.toml"

    cmd = import_linter_plugin._build_command(config_path=config)

    assert_that(cmd).is_equal_to(
        ["lint-imports", "--config", str(config), "--no-logo", "--no-cache"],
    )


def test_doc_url_points_at_contract_types(
    import_linter_plugin: ImportLinterPlugin,
) -> None:
    """A contract name resolves to the contract-types documentation page.

    Args:
        import_linter_plugin: Plugin under test.
    """
    assert_that(import_linter_plugin.doc_url("Layered architecture")).is_equal_to(
        DocUrlTemplate.IMPORT_LINTER,
    )


def test_doc_url_empty_code_returns_none(
    import_linter_plugin: ImportLinterPlugin,
) -> None:
    """An empty contract name has no documentation URL.

    Args:
        import_linter_plugin: Plugin under test.
    """
    assert_that(import_linter_plugin.doc_url("")).is_none()


def test_fix_is_not_supported(import_linter_plugin: ImportLinterPlugin) -> None:
    """import-linter offers no fix mode.

    Args:
        import_linter_plugin: Plugin under test.
    """
    with pytest.raises(NotImplementedError):
        import_linter_plugin.fix(["."], {})


def test_set_options_overrides_timeout(
    import_linter_plugin: ImportLinterPlugin,
) -> None:
    """Runtime options override the default timeout.

    Args:
        import_linter_plugin: Plugin under test.
    """
    import_linter_plugin.set_options(timeout=120)

    assert_that(import_linter_plugin.options["timeout"]).is_equal_to(120)
