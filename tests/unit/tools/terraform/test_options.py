"""Tests for TerraformPlugin options and definition."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.terraform import (
    TERRAFORM_DEFAULT_TIMEOUT,
    TerraformPlugin,
)


def test_definition_name(terraform_plugin: TerraformPlugin) -> None:
    """Definition exposes the terraform name.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
    """
    assert_that(terraform_plugin.definition.name).is_equal_to("terraform")


def test_definition_can_fix(terraform_plugin: TerraformPlugin) -> None:
    """Terraform supports fixing (terraform fmt).

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
    """
    assert_that(terraform_plugin.definition.can_fix).is_true()


def test_definition_tool_type(terraform_plugin: TerraformPlugin) -> None:
    """Terraform is a formatter, linter, and infrastructure tool.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
    """
    tool_type = terraform_plugin.definition.tool_type
    assert_that(bool(tool_type & ToolType.FORMATTER)).is_true()
    assert_that(bool(tool_type & ToolType.LINTER)).is_true()
    assert_that(bool(tool_type & ToolType.INFRASTRUCTURE)).is_true()


def test_definition_file_patterns(terraform_plugin: TerraformPlugin) -> None:
    """Terraform targets .tf and .tfvars files.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
    """
    assert_that(terraform_plugin.definition.file_patterns).contains(
        "*.tf",
        "*.tfvars",
    )


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", TERRAFORM_DEFAULT_TIMEOUT),
        ("validate", True),
    ],
    ids=["timeout_default", "validate_default"],
)
def test_default_options(
    terraform_plugin: TerraformPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options carry expected values.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        option_name: The option to inspect.
        expected_value: The expected default value.
    """
    assert_that(
        terraform_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


@pytest.mark.parametrize(
    "value",
    [True, False],
    ids=["validate_true", "validate_false"],
)
def test_set_options_validate_valid(
    terraform_plugin: TerraformPlugin,
    value: bool,
) -> None:
    """The validate option accepts booleans.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        value: The boolean value to set.
    """
    terraform_plugin.set_options(validate=value)
    assert_that(terraform_plugin.options.get("validate")).is_equal_to(value)


@pytest.mark.parametrize(
    "value",
    ["yes", 1, ["true"]],
    ids=["string", "int", "list"],
)
def test_set_options_validate_invalid(
    terraform_plugin: TerraformPlugin,
    value: object,
) -> None:
    """Non-boolean validate values raise ValueError.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        value: An invalid value for validate.
    """
    with pytest.raises(ValueError, match="validate must be a boolean"):
        terraform_plugin.set_options(validate=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rel_files", "expected"),
    [
        (["main.tf"], ["."]),
        (["infra/main.tf", "infra/vars.tf"], ["infra"]),
        (["a/main.tf", "b/main.tf"], ["a", "b"]),
        (["infra/.terraform/x.tf", "infra/main.tf"], ["infra"]),
        (["notes.tfvars"], []),
    ],
    ids=[
        "root_module",
        "single_module_dedup",
        "two_modules",
        "skips_dot_terraform",
        "tfvars_not_a_module",
    ],
)
def test_module_dirs(
    terraform_plugin: TerraformPlugin,
    rel_files: list[str],
    expected: list[str],
) -> None:
    """Module directory computation dedups, sorts, and skips caches.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        rel_files: Relative file paths to compute modules from.
        expected: Expected sorted module directories.
    """
    assert_that(terraform_plugin._module_dirs(rel_files)).is_equal_to(expected)


def test_doc_url_returns_language_docs(terraform_plugin: TerraformPlugin) -> None:
    """A non-empty code returns the Terraform language docs URL.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
    """
    assert_that(terraform_plugin.doc_url("validate")).is_equal_to(
        "https://developer.hashicorp.com/terraform/language",
    )


def test_doc_url_empty_returns_none(terraform_plugin: TerraformPlugin) -> None:
    """An empty code returns None.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
    """
    assert_that(terraform_plugin.doc_url("")).is_none()
