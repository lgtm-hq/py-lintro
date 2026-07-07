"""Unit tests for KtlintPlugin definition and options."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.ktlint import KtlintPlugin


def test_definition_metadata(ktlint_plugin: KtlintPlugin) -> None:
    """The tool definition advertises the expected metadata.

    Args:
        ktlint_plugin: The plugin under test.
    """
    definition = ktlint_plugin.definition

    assert_that(definition.name).is_equal_to("ktlint")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.file_patterns).contains("*.kt", "*.kts")
    assert_that(definition.native_configs).contains(".editorconfig")
    assert_that(bool(definition.tool_type & ToolType.LINTER)).is_true()
    assert_that(bool(definition.tool_type & ToolType.FORMATTER)).is_true()


def test_set_valid_code_style(ktlint_plugin: KtlintPlugin) -> None:
    """A valid code style is normalized and stored.

    Args:
        ktlint_plugin: The plugin under test.
    """
    ktlint_plugin.set_options(code_style="KTLINT_OFFICIAL")

    assert_that(ktlint_plugin.options["code_style"]).is_equal_to("ktlint_official")


def test_set_invalid_code_style_raises(ktlint_plugin: KtlintPlugin) -> None:
    """An invalid code style raises ValueError.

    Args:
        ktlint_plugin: The plugin under test.
    """
    with pytest.raises(ValueError, match="Invalid code style"):
        ktlint_plugin.set_options(code_style="pep8")


def test_common_args_include_code_style_and_editorconfig(
    ktlint_plugin: KtlintPlugin,
) -> None:
    """Configured options are rendered as ktlint CLI arguments.

    Args:
        ktlint_plugin: The plugin under test.
    """
    ktlint_plugin.set_options(
        code_style="android_studio",
        editorconfig="/tmp/.editorconfig",
    )

    args = ktlint_plugin._build_common_args()

    assert_that(args).contains("--log-level=error")
    assert_that(args).contains("--code-style=android_studio")
    assert_that(args).contains("--editorconfig=/tmp/.editorconfig")


def test_common_args_default_minimal(ktlint_plugin: KtlintPlugin) -> None:
    """With no options set, only the log-level flag is emitted.

    Args:
        ktlint_plugin: The plugin under test.
    """
    args = ktlint_plugin._build_common_args()

    assert_that(args).is_equal_to(["--log-level=error"])


@pytest.mark.parametrize(
    ("code", "expected_ruleset"),
    [
        ("standard:filename", "standard"),
        ("experimental:foo", "experimental"),
        ("unknown:bar", "standard"),
        ("bare-rule", "standard"),
    ],
)
def test_doc_url_routes_by_ruleset(
    ktlint_plugin: KtlintPlugin,
    code: str,
    expected_ruleset: str,
) -> None:
    """doc_url routes to the correct ruleset documentation page.

    Args:
        ktlint_plugin: The plugin under test.
        code: The rule id.
        expected_ruleset: The ruleset segment expected in the URL.
    """
    url = ktlint_plugin.doc_url(code)

    assert_that(url).is_not_none()
    assert_that(url).contains(f"/rules/{expected_ruleset}/")


def test_doc_url_empty_code_returns_none(ktlint_plugin: KtlintPlugin) -> None:
    """An empty rule id yields no documentation URL.

    Args:
        ktlint_plugin: The plugin under test.
    """
    assert_that(ktlint_plugin.doc_url("")).is_none()
