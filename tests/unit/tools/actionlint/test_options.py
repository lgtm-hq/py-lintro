"""Unit tests for actionlint plugin options."""

from __future__ import annotations

from assertpy import assert_that

from lintro.tools.definitions.actionlint import (
    ACTIONLINT_DEFAULT_TIMEOUT,
    ActionlintPlugin,
)


def test_default_options(actionlint_plugin: ActionlintPlugin) -> None:
    """Default options include the production timeout.

    Args:
        actionlint_plugin: The ActionlintPlugin instance to test.
    """
    defaults = actionlint_plugin.definition.default_options
    assert_that(defaults["timeout"]).is_equal_to(ACTIONLINT_DEFAULT_TIMEOUT)


def test_set_options_timeout(actionlint_plugin: ActionlintPlugin) -> None:
    """set_options stores a custom timeout.

    Args:
        actionlint_plugin: The ActionlintPlugin instance to test.
    """
    actionlint_plugin.set_options(timeout=45)
    assert_that(actionlint_plugin.options.get("timeout")).is_equal_to(45)
