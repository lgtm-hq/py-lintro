"""A tool that did not run never reads as clean in the review prompt (#3497).

With ``--with-lint`` the review prompt carries a digest of the linters'
findings. A tool lintro refused to run, or one that crashed or timed out,
has no findings to show; without a note its silence would read as a clean
result, and it would be counted among the tools that ran.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from assertpy import assert_that

from lintro.ai.review import lint_bridge, preparation_resolvers
from lintro.ai.review.lint_bridge import format_lint_results_for_prompt
from lintro.ai.review.lint_completeness import count_lint_results
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.config.lintro_config import LintroConfig
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.tools import tool_manager
from tests.refused_plugin import FINDING_MESSAGE, RefusingPlugin, copy_configured


@dataclass
class _CrashingPlugin(RefusingPlugin):
    """Plugin whose check raises an ordinary exception."""

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Crash.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            Never; it always raises.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("tsc exploded")


def _run_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tools: dict[str, BaseToolPlugin],
) -> list[ToolResult]:
    """Run *tools* through the review's lint bridge over one changed file.

    Args:
        monkeypatch: Pytest patch fixture.
        tools: Plugins by name.

    Returns:
        The bridge's results.
    """
    monkeypatch.setattr(
        lint_bridge,
        "get_tools_to_run",
        lambda **_: SimpleNamespace(to_run=list(tools)),
    )
    monkeypatch.setattr(
        lint_bridge,
        "UnifiedConfigManager",
        lambda: SimpleNamespace(
            get_tool_config=lambda _: SimpleNamespace(lintro_tool_config={}),
        ),
    )
    monkeypatch.setattr(lint_bridge, "resolve_python_formatting", lambda *_, **__: None)
    monkeypatch.setattr(tool_manager, "get_tool", tools.__getitem__)
    monkeypatch.setattr(lint_bridge, "configure_tool_for_execution", copy_configured)
    return lint_bridge.run_lint_on_changed_files(
        changed_files=["src/app.ts"],
        lintro_config=LintroConfig(),
    )


def _healthy() -> RefusingPlugin:
    """Build a plugin whose command runs and passes.

    Returns:
        The plugin.
    """
    return RefusingPlugin(tool_name="eslint", command=(sys.executable, "-c", "pass"))


@pytest.mark.parametrize("with_finding", [False, True], ids=["no-findings", "finding"])
def test_refused_tool_is_named_in_the_prompt_and_not_counted_as_ran(
    monkeypatch: pytest.MonkeyPatch,
    with_finding: bool,
) -> None:
    """The digest names the refused tool and keeps its real findings.

    Args:
        monkeypatch: Pytest patch fixture.
        with_finding: Whether the refused tool also reported a finding.
    """
    results = _run_bridge(
        monkeypatch,
        {"tsc": RefusingPlugin(with_finding=with_finding), "eslint": _healthy()},
    )

    digest = format_lint_results_for_prompt(results=results)
    counts = count_lint_results(results)

    assert_that(digest).starts_with("<lint_results>\n")
    assert_that(digest).contains(
        "Tool: tsc | did not run / incomplete: lintro refused to run tsc: "
        "Refusing to pass argument 1",
    )
    assert_that(digest).does_not_contain("eslint")
    assert_that(FINDING_MESSAGE in digest).is_equal_to(with_finding)
    assert_that((counts.ran, counts.incomplete, counts.issues)).is_equal_to(
        (1, 1, int(with_finding)),
    )


def test_crashed_tool_is_named_in_the_prompt_instead_of_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check that raises is reported as incomplete, not silently skipped.

    Args:
        monkeypatch: Pytest patch fixture.
    """
    results = _run_bridge(monkeypatch, {"tsc": _CrashingPlugin()})

    digest = format_lint_results_for_prompt(results=results)

    assert_that(digest).contains(
        "Tool: tsc | did not run / incomplete: check crashed: "
        "RuntimeError: tsc exploded",
    )
    assert_that(count_lint_results(results).incomplete).is_equal_to(1)


@pytest.mark.parametrize(
    ("result", "note"),
    [
        pytest.param(
            ToolResult(name="tsc", success=False, timed_out=True, output="x"),
            "Tool: tsc | did not run / incomplete: timed out",
            id="timed-out",
        ),
        pytest.param(
            ToolResult(
                name="tsc",
                success=False,
                execution_failed=True,
                output="\x1b]0;title\x07\x1b[31mnpx cache changed\x1b[0m\nmore",
            ),
            "Tool: tsc | did not run / incomplete: npx cache changed\n",
            id="control-codes-cleaned",
        ),
        pytest.param(
            ToolResult(name="tsc", success=False),
            "Tool: tsc | did not run / incomplete: failed with no findings",
            id="failed-without-findings",
        ),
    ],
)
def test_incomplete_result_gets_one_cleaned_line(result: ToolResult, note: str) -> None:
    """Each incomplete result gets one line naming why.

    Args:
        result: An incomplete result.
        note: The line the digest must carry.
    """
    digest = format_lint_results_for_prompt(results=[result])

    assert_that(digest).contains(note)
    assert_that(digest).does_not_contain("\x1b")


def test_a_skipped_tool_is_counted_neither_as_ran_nor_as_incomplete() -> None:
    """A skip made no claim, so it is not a tool that ran to a verdict."""
    results = [
        ToolResult(name="ruff", success=True),
        ToolResult(name="tsc", success=True, skipped=True, skip_reason="deferred"),
    ]

    counts = count_lint_results(results)

    assert_that((counts.ran, counts.incomplete)).is_equal_to((1, 0))
    assert_that(format_lint_results_for_prompt(results=results)).is_empty()


def test_a_clean_legacy_result_is_not_called_incomplete() -> None:
    """``findings_complete`` alone is not used: legacy plugins never set it."""
    clean = ToolResult(name="ruff", success=True, findings_complete=False)

    assert_that(format_lint_results_for_prompt(results=[clean])).is_empty()
    assert_that(count_lint_results([clean]).ran).is_equal_to(1)


def test_review_digest_carries_a_refused_tool_with_no_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--with-lint`` with only a refused tool still produces a lint section.

    Args:
        monkeypatch: Pytest patch fixture.
    """
    monkeypatch.setattr(
        preparation_resolvers,
        "run_lint_on_changed_files",
        lambda **_: [
            ToolResult(
                name="tsc",
                success=False,
                execution_failed=True,
                output="lintro refused to run tsc: unsafe argument",
            ),
        ],
    )
    context = ReviewContext(
        base_ref="main",
        head_ref="abc123",
        changed_files=[
            ChangedFile(path="src/app.ts", status="modified", additions=1, deletions=0),
        ],
        unified_diff="",
    )

    digest, counts = preparation_resolvers.build_lint_digest(
        context=context,
        lintro_config=LintroConfig(),
    )

    assert_that(digest).is_equal_to(
        "<lint_results>\n"
        "Tool: tsc | did not run / incomplete: "
        "lintro refused to run tsc: unsafe argument\n"
        "</lint_results>",
    )
    assert_that((counts.ran, counts.incomplete, counts.issues)).is_equal_to((0, 1, 0))
