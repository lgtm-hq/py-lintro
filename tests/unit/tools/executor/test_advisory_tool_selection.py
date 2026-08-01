"""Tool-selection tests for advisory (AI-finder) tools under chk/fmt.

Advisory tools moved out of ``lintro chk`` in #1308: they must never appear
in a default or ``--tools all`` run, and naming one explicitly must fail with
a pointer to ``lintro review`` rather than silently doing nothing.
"""

from __future__ import annotations

import subprocess  # nosec B404 - runs a fixed argv against this interpreter
import sys

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.utils.execution.tool_configuration import get_tools_to_run

_AI_MODULES_SNIPPET = (
    "import sys; "
    "from lintro.plugins.discovery import discover_all_tools; "
    "discover_all_tools(); "
    "print(len([m for m in sys.modules if m.startswith('lintro.ai')]))"
)


def test_default_check_run_excludes_advisory_tools() -> None:
    """A default check run never selects an advisory tool."""
    result = get_tools_to_run(tools=None, action=Action.CHECK)

    assert_that(result.to_run).does_not_contain("idiom-review")
    assert_that([tool.name for tool in result.skipped]).does_not_contain(
        "idiom-review",
    )


def test_tools_all_check_run_excludes_advisory_tools() -> None:
    """``--tools all`` is still deterministic-only."""
    result = get_tools_to_run(tools="all", action=Action.CHECK)

    assert_that(result.to_run).does_not_contain("idiom-review")


def test_fmt_run_excludes_advisory_tools() -> None:
    """A default fmt run never selects an advisory tool."""
    result = get_tools_to_run(tools=None, action=Action.FIX)

    assert_that(result.to_run).does_not_contain("idiom-review")


def test_explicit_advisory_tool_errors_with_review_pointer() -> None:
    """``chk --tools idiom-review`` fails and names the review verb."""
    with pytest.raises(ValueError) as excinfo:
        get_tools_to_run(tools="idiom-review", action=Action.CHECK)

    message = str(excinfo.value)
    assert_that(message).contains("advisory")
    assert_that(message).contains("lintro review --advisory-tools idiom-review")


def test_explicit_advisory_tool_errors_for_underscore_spelling() -> None:
    """The alias spelling errors the same way as the registered name."""
    with pytest.raises(ValueError, match="lintro review"):
        get_tools_to_run(tools="idiom_review", action=Action.CHECK)


def test_deterministic_tool_still_selectable() -> None:
    """Classifying advisory tools does not disturb deterministic ones."""
    result = get_tools_to_run(tools="ruff", action=Action.CHECK)

    assert_that(result.to_run).is_equal_to(["ruff"])


def test_plugin_discovery_loads_no_ai_modules() -> None:
    """Discovery must not drag the AI stack into chk-only runs (#1305).

    Runs in a fresh interpreter because the in-process ``sys.modules`` is
    polluted by other tests that legitimately import the AI layer.
    """
    completed = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [sys.executable, "-c", _AI_MODULES_SNIPPET],
        capture_output=True,
        text=True,
        check=True,
    )

    assert_that(completed.stdout.strip().splitlines()[-1]).is_equal_to("0")
