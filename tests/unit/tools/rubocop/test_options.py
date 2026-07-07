"""Unit tests for RubocopPlugin definition, options, and doc URLs."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.rubocop import RubocopPlugin


def test_definition_metadata(rubocop_plugin: RubocopPlugin) -> None:
    """The tool definition advertises linter+formatter with fix support.

    Args:
        rubocop_plugin: The plugin under test.
    """
    definition = rubocop_plugin.definition
    assert_that(definition.name).is_equal_to("rubocop")
    assert_that(definition.can_fix).is_true()
    assert_that(bool(definition.tool_type & ToolType.LINTER)).is_true()
    assert_that(bool(definition.tool_type & ToolType.FORMATTER)).is_true()
    assert_that(definition.file_patterns).contains("*.rb")
    assert_that(definition.native_configs).contains(".rubocop.yml")


def test_default_unsafe_fixes_off(rubocop_plugin: RubocopPlugin) -> None:
    """Safe autocorrect is the default (unsafe_fixes off).

    Args:
        rubocop_plugin: The plugin under test.
    """
    assert_that(rubocop_plugin.definition.default_options["unsafe_fixes"]).is_false()


def test_fix_command_safe_by_default(rubocop_plugin: RubocopPlugin) -> None:
    """The fix command uses --autocorrect (safe) by default.

    Args:
        rubocop_plugin: The plugin under test.
    """
    cmd = rubocop_plugin._build_fix_command(["a.rb"])
    assert_that(cmd).contains("--autocorrect")
    assert_that(cmd).does_not_contain("--autocorrect-all")


def test_fix_command_unsafe_when_enabled(rubocop_plugin: RubocopPlugin) -> None:
    """Enabling unsafe_fixes switches to --autocorrect-all.

    Args:
        rubocop_plugin: The plugin under test.
    """
    rubocop_plugin.set_options(unsafe_fixes=True)
    cmd = rubocop_plugin._build_fix_command(["a.rb"])
    assert_that(cmd).contains("--autocorrect-all")


def test_set_options_rejects_non_bool(rubocop_plugin: RubocopPlugin) -> None:
    """A non-boolean unsafe_fixes value raises.

    Args:
        rubocop_plugin: The plugin under test.
    """
    with pytest.raises((ValueError, TypeError)):
        rubocop_plugin.set_options(unsafe_fixes="yes")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "expected_fragment"),
    [
        ("Layout/SpaceInsideParens", "cops_layout.html#layoutspaceinsideparens"),
        ("Style/StringLiterals", "cops_style.html#stylestringliterals"),
        ("Lint/UselessAssignment", "cops_lint.html#lintuselessassignment"),
    ],
)
def test_doc_url(
    rubocop_plugin: RubocopPlugin,
    code: str,
    expected_fragment: str,
) -> None:
    """doc_url builds a department page URL with a cop anchor.

    Args:
        rubocop_plugin: The plugin under test.
        code: The cop name.
        expected_fragment: The expected trailing URL fragment.
    """
    url = rubocop_plugin.doc_url(code)
    assert_that(url).is_not_none()
    assert_that(url).contains(expected_fragment)
    assert_that(url).starts_with("https://docs.rubocop.org/rubocop/")


def test_doc_url_none_for_department_less_code(rubocop_plugin: RubocopPlugin) -> None:
    """doc_url returns None when the code has no department prefix.

    Args:
        rubocop_plugin: The plugin under test.
    """
    assert_that(rubocop_plugin.doc_url("CustomCop")).is_none()
    assert_that(rubocop_plugin.doc_url("")).is_none()
