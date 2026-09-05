"""Tests for AI enhancement of a completed run and its exit-code effects.

Since issue #1823 the executor takes no AI callables at all. The CLI and the
public API call :func:`lintro.utils.tool_executor.execute_run`, hand the
resulting artifact to :func:`lintro.ai.interface.enhance_artifact`, and render
whatever comes back. These tests drive that hand-off through
:func:`lintro.api.pipeline.run_lint_artifact`.
"""

from __future__ import annotations

from typing import Any

from assertpy import assert_that

import lintro.ai.interface as ai_interface
import lintro.utils.execution.run_aggregation as run_aggregation
import lintro.utils.tool_executor as te
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.interface import AIOutcome
from lintro.api.pipeline import run_lint_artifact
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig
from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.tool_configuration import ToolsToRunResult


class _FakeTool:
    """Minimal tool double reporting a single unfixed issue."""

    def set_options(self, **_kwargs: Any) -> None:
        """Accept and ignore runtime options."""
        return None

    def reset_options(self) -> None:
        """Accept and ignore option resets."""
        return None

    def fix(self, _paths: Any, _options: Any) -> ToolResult:
        """Return a fix result with one remaining issue.

        Args:
            _paths: Ignored paths.
            _options: Ignored options.

        Returns:
            A failing :class:`ToolResult` with one remaining issue.
        """
        return ToolResult(
            name="ruff",
            success=False,
            issues_count=1,
            fixed_issues_count=0,
            remaining_issues_count=1,
            issues=[],
        )

    def check(self, _paths: Any, _options: Any) -> ToolResult:
        """Return a check result with one issue.

        Args:
            _paths: Ignored paths.
            _options: Ignored options.

        Returns:
            A failing :class:`ToolResult` with one issue.
        """
        return ToolResult(
            name="ruff",
            success=False,
            issues_count=1,
            issues=[],
        )


def _install_executor_doubles(
    monkeypatch: Any,
    fake_logger: Any,
    lintro_config: LintroConfig,
) -> None:
    """Patch out the executor's environment so only the hand-off is exercised.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        fake_logger: Console logger double.
        lintro_config: Configuration returned by ``get_config``.
    """
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda tools, action, **_kw: ToolsToRunResult(to_run=["ruff"]),
    )
    monkeypatch.setattr(
        te.tool_manager,  # type: ignore[attr-defined]  # singleton
        "get_tool",
        lambda name: _FakeTool(),
    )
    monkeypatch.setattr(
        te,
        "configure_tool_for_execution",
        lambda *, tool, **kwargs: tool,
    )
    monkeypatch.setattr(
        te,
        "execute_post_checks",
        lambda **kwargs: (
            kwargs["total_issues"],
            kwargs["total_fixed"],
            kwargs["total_remaining"],
        ),
    )

    import lintro.config.config_loader as config_loader
    import lintro.utils.console as console_module
    import lintro.utils.logger_setup as logger_setup
    from lintro.utils.output import OutputManager

    monkeypatch.setattr(config_loader, "get_config", lambda: lintro_config)
    monkeypatch.setattr(
        console_module,
        "create_logger",
        lambda **kwargs: fake_logger,
    )
    monkeypatch.setattr(
        logger_setup,
        "setup_execution_logging",
        lambda run_dir, debug=False: None,
    )
    monkeypatch.setattr(
        OutputManager,
        "write_reports_from_results",
        lambda self, results: None,
    )
    monkeypatch.setattr(te, "load_post_checks_config", lambda: {"enabled": False})


def _ai_enabled_config() -> LintroConfig:
    """Build a config with AI enabled and serial execution.

    Returns:
        A :class:`LintroConfig` with AI enabled.
    """
    return LintroConfig(
        execution=ExecutionConfig(parallel=False),
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            auto_apply=True,
        ).model_dump(),
    )


def _fix_results_in_place(all_results: list[ToolResult]) -> None:
    """Mark every result as fully fixed, mimicking AI auto-apply.

    Args:
        all_results: Results mutated in place.
    """
    for result in all_results:
        result.success = True
        result.fixed_issues_count = result.issues_count
        result.remaining_issues_count = 0
        result.issues_count = 0


def _run_pipeline(**kwargs: Any) -> int:
    """Invoke the AI-aware pipeline with the shared fixture arguments.

    Args:
        **kwargs: Extra keyword arguments forwarded to the pipeline.

    Returns:
        The exit code the pipeline resolved to.
    """
    call_kwargs: dict[str, Any] = {
        "action": "fmt",
        "paths": ["."],
        "tools": "ruff",
        "tool_options": None,
        "exclude": None,
        "include_venv": False,
        "group_by": "auto",
        "output_format": "json",
        "verbose": False,
        "raw_output": False,
    }
    call_kwargs.update(kwargs)
    return run_lint_artifact(**call_kwargs).exit_code


def _install_ai_layer(monkeypatch: Any, runner: Any) -> None:
    """Replace the AI layer entry point used by ``enhance_artifact``.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        runner: Callable standing in for ``run_ai_layer``.
    """
    monkeypatch.setattr(ai_interface, "run_ai_layer", runner)


def test_fix_recomputes_totals_after_ai_changes(monkeypatch, fake_logger):
    """Totals are re-aggregated when the AI layer reports that it ran."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _runner(*, all_results: list[ToolResult], **_kwargs: Any) -> AIOutcome:
        _fix_results_in_place(all_results)
        return AIOutcome(ran=True, force_failure=False)

    _install_ai_layer(monkeypatch, _runner)

    captured: dict[str, int] = {}

    def _capture_exit_code(
        *,
        action,
        all_results,
        total_issues,
        total_remaining,
        main_phase_empty_due_to_filter,
    ):
        captured["total_issues"] = total_issues
        captured["total_remaining"] = total_remaining
        return 0 if total_remaining == 0 else 1

    monkeypatch.setattr(run_aggregation, "determine_exit_code", _capture_exit_code)

    exit_code = _run_pipeline()

    assert_that(exit_code).is_equal_to(0)
    assert_that(captured.get("total_issues")).is_equal_to(0)
    assert_that(captured.get("total_remaining")).is_equal_to(0)


def test_ai_disabled_means_no_ai_and_unchanged_exit_code(monkeypatch, fake_logger):
    """With ``ai_enabled=False`` the pipeline never constructs the AI hook."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    import lintro.ai.hook as hook_module

    def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("pipeline must not construct the AI hook")

    monkeypatch.setattr(hook_module, "AIPostExecutionHook", _fail)

    captured: dict[str, int] = {}

    def _capture_exit_code(
        *,
        action,
        all_results,
        total_issues,
        total_remaining,
        main_phase_empty_due_to_filter,
    ):
        captured["total_remaining"] = total_remaining
        return 0 if total_remaining == 0 else 1

    monkeypatch.setattr(run_aggregation, "determine_exit_code", _capture_exit_code)

    exit_code = _run_pipeline(ai_enabled=False)

    assert_that(exit_code).is_equal_to(1)
    assert_that(captured.get("total_remaining")).is_equal_to(1)


def test_executor_alone_runs_no_ai(monkeypatch, fake_logger):
    """``run_lint_tools_simple`` is AI-free: the AI hook is never built."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    import lintro.ai.hook as hook_module

    def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the executor must not construct the AI hook")

    monkeypatch.setattr(hook_module, "AIPostExecutionHook", _fail)

    exit_code = te.run_lint_tools_simple(
        action="fmt",
        paths=["."],
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="json",
        verbose=False,
    )

    assert_that(exit_code).is_equal_to(1)


def test_ai_force_failure_overrides_clean_exit_code(monkeypatch, fake_logger):
    """A forcing AI outcome turns a clean run into exit code 1."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _runner(*, all_results: list[ToolResult], **_kwargs: Any) -> AIOutcome:
        _fix_results_in_place(all_results)
        return AIOutcome(ran=True, force_failure=True)

    _install_ai_layer(monkeypatch, _runner)
    monkeypatch.setattr(run_aggregation, "determine_exit_code", lambda **_kw: 0)

    assert_that(_run_pipeline()).is_equal_to(1)


def test_ai_without_force_failure_leaves_exit_code(monkeypatch, fake_logger):
    """A non-forcing AI outcome does not change the computed exit code."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _runner(*, all_results: list[ToolResult], **_kwargs: Any) -> AIOutcome:
        _fix_results_in_place(all_results)
        return AIOutcome(ran=True, force_failure=False)

    _install_ai_layer(monkeypatch, _runner)
    monkeypatch.setattr(run_aggregation, "determine_exit_code", lambda **_kw: 0)

    assert_that(_run_pipeline()).is_equal_to(0)


def test_ai_exception_propagates(monkeypatch, fake_logger):
    """Errors raised by the AI layer propagate, as ``fail_on_ai_error`` needs."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _runner(**_kwargs: Any) -> AIOutcome:
        raise RuntimeError("provider exploded")

    _install_ai_layer(monkeypatch, _runner)

    assert_that(_run_pipeline).raises(RuntimeError).when_called_with()


def test_ai_status_lines_reach_the_summary(monkeypatch, fake_logger):
    """The pipeline renders AI status rows into the configuration summary."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)
    monkeypatch.setattr(run_aggregation, "determine_exit_code", lambda **_kw: 0)
    _install_ai_layer(monkeypatch, lambda **_kw: AIOutcome(ran=False))

    captured: dict[str, Any] = {}

    import lintro.utils.console.pre_execution_summary as summary_module

    def _fake_summary(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        summary_module,
        "print_pre_execution_summary",
        _fake_summary,
    )
    monkeypatch.setattr(
        ai_interface,
        "render_ai_status",
        lambda *, ai_config, is_ci: ["[green]enabled[/green]"],
    )

    exit_code = _run_pipeline(output_format="grid")

    assert_that(exit_code).is_equal_to(0)
    assert_that(captured.get("ai_status_lines")).is_equal_to(["[green]enabled[/green]"])
