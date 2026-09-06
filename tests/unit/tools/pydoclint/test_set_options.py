"""Tests for pydoclint plugin set_options method.

The simplified plugin uses the base class set_options method.
Pydoclint reads most configuration directly from pyproject.toml.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.tools.pydoclint.definition import PydoclintPlugin


def test_set_options_quiet(pydoclint_plugin: PydoclintPlugin) -> None:
    """Set quiet option correctly.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
    """
    pydoclint_plugin.set_options(quiet=False)
    assert_that(pydoclint_plugin.options.get("quiet")).is_equal_to(False)


def test_set_options_timeout(pydoclint_plugin: PydoclintPlugin) -> None:
    """Set timeout option correctly.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
    """
    pydoclint_plugin.set_options(timeout=60)
    assert_that(pydoclint_plugin.options.get("timeout")).is_equal_to(60)
