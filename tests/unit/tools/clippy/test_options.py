"""Unit tests for clippy plugin options."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.definitions.clippy import CLIPPY_DEFAULT_TIMEOUT, ClippyPlugin


def test_default_options(clippy_plugin: ClippyPlugin) -> None:
    """Default options include the production timeout.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    defaults = clippy_plugin.definition.default_options
    assert_that(defaults["timeout"]).is_equal_to(CLIPPY_DEFAULT_TIMEOUT)


def test_set_options_timeout(clippy_plugin: ClippyPlugin) -> None:
    """Set timeout option.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    clippy_plugin.set_options(timeout=60)
    assert_that(clippy_plugin.options.get("timeout")).is_equal_to(60)


def test_set_options_invalid_timeout(clippy_plugin: ClippyPlugin) -> None:
    """Raise ValueError for a non-positive timeout.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    with pytest.raises(ValueError, match="timeout must be"):
        clippy_plugin.set_options(timeout=-1)


def test_doc_url_formats_lint_name(clippy_plugin: ClippyPlugin) -> None:
    """doc_url formats the Clippy lint name into a URL.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    url = clippy_plugin.doc_url("needless_return")
    assert_that(url).contains("needless_return")
