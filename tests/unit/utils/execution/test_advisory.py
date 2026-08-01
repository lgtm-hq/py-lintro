"""Tests for the advisory (AI-finder) tool runner used by ``lintro review``."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from assertpy import assert_that

from lintro.config.lintro_config import LintroConfig, LintroToolConfig
from lintro.enums.execution_class import (
    ExecutionClass,
    normalize_execution_class,
)
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.protocol import ToolDefinition
from lintro.utils.execution import advisory as advisory_module
from lintro.utils.execution.advisory import (
    advisory_findings_count,
    advisory_results_to_payload,
    get_advisory_tool_names,
    render_advisory_results,
    resolve_advisory_tools,
    run_advisory_tools,
)


def test_execution_class_defaults_to_deterministic() -> None:
    """A tool definition is deterministic unless it opts into advisory."""
    definition = ToolDefinition(name="demo", description="demo tool")

    assert_that(definition.execution_class).is_equal_to(ExecutionClass.DETERMINISTIC)
    assert_that(definition.is_advisory).is_false()


def test_execution_class_advisory_flag() -> None:
    """An advisory definition reports ``is_advisory``."""
    definition = ToolDefinition(
        name="demo",
        description="demo tool",
        execution_class=ExecutionClass.ADVISORY,
    )

    assert_that(definition.is_advisory).is_true()


def test_normalize_execution_class_accepts_strings() -> None:
    """String values normalize case-insensitively."""
    assert_that(normalize_execution_class("ADVISORY")).is_equal_to(
        ExecutionClass.ADVISORY,
    )
    assert_that(
        normalize_execution_class(ExecutionClass.DETERMINISTIC),
    ).is_equal_to(ExecutionClass.DETERMINISTIC)


def test_normalize_execution_class_rejects_unknown() -> None:
    """An unknown execution class raises."""
    with pytest.raises(ValueError, match="Unknown execution class"):
        normalize_execution_class("opinionated")


def test_idiom_review_is_the_registered_advisory_tool() -> None:
    """idiom-review is classified advisory and discoverable as such."""
    assert_that(get_advisory_tool_names()).contains("idiom-review")


def test_resolve_advisory_tools_defaults_to_all() -> None:
    """The default selection picks up every enabled advisory tool."""
    selection = resolve_advisory_tools(requested=None)

    assert_that(selection.to_run).contains("idiom-review")


def test_resolve_advisory_tools_none_selects_nothing() -> None:
    """``none`` disables advisory execution."""
    selection = resolve_advisory_tools(requested="none")

    assert_that(selection.to_run).is_empty()


def test_resolve_advisory_tools_accepts_underscore_spelling() -> None:
    """Underscore spellings resolve to the registered hyphenated name."""
    selection = resolve_advisory_tools(requested="idiom_review")

    assert_that(selection.to_run).is_equal_to(["idiom-review"])


def test_resolve_advisory_tools_rejects_deterministic_tool() -> None:
    """Naming a deterministic tool points the user back at chk."""
    with pytest.raises(ValueError, match="lintro chk --tools ruff"):
        resolve_advisory_tools(requested="ruff")


def test_resolve_advisory_tools_rejects_unknown_tool() -> None:
    """An unknown advisory tool name raises."""
    with pytest.raises(ValueError, match="Unknown advisory tool"):
        resolve_advisory_tools(requested="does-not-exist")


def test_run_advisory_tools_without_tools_is_a_noop(tmp_path: Path) -> None:
    """No selected tools means no results and no work."""
    assert_that(
        run_advisory_tools(paths=[str(tmp_path)], tool_names=[]),
    ).is_empty()


def test_run_advisory_tools_reports_tool_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising advisory tool becomes a failed result, not an abort."""

    class _Boom:
        def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        advisory_module,
        "configure_tool_for_execution",
        lambda **_kwargs: _Boom(),
    )
    results = run_advisory_tools(
        paths=[str(tmp_path)],
        tool_names=["idiom-review"],
    )

    assert_that(results).is_length(1)
    assert_that(results[0].success).is_false()
    assert_that(results[0].output).contains("provider exploded")


def test_advisory_findings_count_ignores_skipped_results() -> None:
    """Skipped advisory tools contribute no findings."""
    results = [
        ToolResult(
            name="a",
            skipped=True,
            skip_reason="opt-in required",
            issues_count=5,
        ),
        ToolResult(name="b", success=False, issues_count=2),
    ]

    assert_that(advisory_findings_count(results)).is_equal_to(2)


def test_render_advisory_results_reports_skip_reason() -> None:
    """A skipped advisory tool renders its reason."""
    rendered = render_advisory_results(
        results=[
            ToolResult(
                name="idiom-review",
                skipped=True,
                skip_reason="opt-in required",
            ),
        ],
    )

    assert_that(rendered).contains("Advisory: idiom-review")
    assert_that(rendered).contains("opt-in required")


def test_render_advisory_results_empty_is_blank() -> None:
    """No results render as an empty string."""
    assert_that(render_advisory_results(results=[])).is_equal_to("")


def test_advisory_results_to_payload_is_json_shaped() -> None:
    """The payload exposes per-tool counts and display rows."""
    from lintro.parsers.idiom_review.idiom_review_issue import IdiomReviewIssue

    issue = IdiomReviewIssue(
        file="a.py",
        line=3,
        message="prefer any()",
        code="idiom/python/prefer-any",
    )
    payload = advisory_results_to_payload(
        [
            ToolResult(
                name="idiom-review",
                success=False,
                issues_count=1,
                issues=[issue],
            ),
        ],
    )

    assert_that(payload).is_length(1)
    assert_that(payload[0]["tool"]).is_equal_to("idiom-review")
    assert_that(payload[0]["issues_count"]).is_equal_to(1)
    issues = cast("list[dict[str, str]]", payload[0]["issues"])
    assert_that(issues[0]["file"]).is_equal_to("a.py")


def test_run_advisory_tools_survives_configuration_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool that raises during configuration does not abort the run."""

    def _explode(**_kwargs: object) -> object:
        raise RuntimeError("config exploded")

    monkeypatch.setattr(
        advisory_module,
        "configure_tool_for_execution",
        _explode,
    )
    results = run_advisory_tools(
        paths=[str(tmp_path)],
        tool_names=["idiom-review"],
    )

    assert_that(results).is_length(1)
    assert_that(results[0].success).is_false()
    assert_that(results[0].output).contains("config exploded")


def test_resolve_advisory_tools_honors_config_disabled_tool() -> None:
    """A config-disabled advisory tool is skipped in the default selection."""
    config = LintroConfig(tools={"idiom-review": LintroToolConfig(enabled=False)})

    selection = resolve_advisory_tools(requested=None, lintro_config=config)

    assert_that(selection.to_run).does_not_contain("idiom-review")
    assert_that([tool.name for tool in selection.skipped]).contains("idiom-review")


def test_resolve_advisory_tools_honors_config_disabled_explicit_request() -> None:
    """An explicitly named but config-disabled tool is skipped, not run."""
    config = LintroConfig(tools={"idiom-review": LintroToolConfig(enabled=False)})

    selection = resolve_advisory_tools(
        requested="idiom-review",
        lintro_config=config,
    )

    assert_that(selection.to_run).is_empty()
    assert_that(selection.skipped[0].reason).contains("disabled in config")
