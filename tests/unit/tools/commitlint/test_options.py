"""Unit tests for commitlint plugin options and definition defaults."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.definitions.commitlint import CommitlintPlugin


def test_default_options(commitlint_plugin: CommitlintPlugin) -> None:
    """The definition exposes the expected default options."""
    defaults = commitlint_plugin.definition.default_options
    assert_that(defaults).contains_key("timeout")
    assert_that(defaults["timeout"]).is_equal_to(30)
    assert_that(commitlint_plugin.definition.default_timeout).is_equal_to(30)


def test_set_options_timeout(commitlint_plugin: CommitlintPlugin) -> None:
    """A valid timeout is stored on the plugin options."""
    commitlint_plugin.set_options(timeout=60)
    assert_that(commitlint_plugin.options["timeout"]).is_equal_to(60)


def test_set_options_rejects_non_positive_timeout(
    commitlint_plugin: CommitlintPlugin,
) -> None:
    """A non-positive timeout is rejected by validation."""
    with pytest.raises((ValueError, TypeError)):
        commitlint_plugin.set_options(timeout=0)


def test_command_prefers_direct_binary(
    commitlint_plugin: CommitlintPlugin,
) -> None:
    """The command builder returns a commitlint invocation prefix."""
    cmd = commitlint_plugin._get_commitlint_command()
    assert_that(cmd[-1]).is_equal_to("commitlint")
