"""Tests for the executor's AI seam and AI-driven exit-code behavior.

The executor itself no longer imports :mod:`lintro.ai` (issue #724 PR 2). It
consumes an injected ``ai_runner`` returning a core
:class:`~lintro.models.core.ai_seam.AIOutcome`, so these tests exercise the
seam rather than the AI layer.
"""

from __future__ import annotations

from typing import Any

from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig
from lintro.models.core.ai_seam import AIOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.tool_configuration import ToolsToRunResult
from lintro.utils.tool_executor import run_lint_tools_simple


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
    """Patch out the executor's environment so only the seam is exercised.

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
        ),
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


def _run_executor(**kwargs: Any) -> int:
    """Invoke the executor with the shared fixture arguments.

    Args:
        **kwargs: Extra keyword arguments forwarded to the executor.

    Returns:
        The exit code returned by the executor.
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
    return run_lint_tools_simple(**call_kwargs)


def test_fix_recomputes_totals_after_ai_runner_changes(monkeypatch, fake_logger):
    """Totals are re-aggregated when the injected AI runner reports it ran."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _ai_runner(*, all_results: list[ToolResult], **_kwargs: Any) -> AIOutcome:
        _fix_results_in_place(all_results)
        return AIOutcome(ran=True, force_failure=False)

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

    monkeypatch.setattr(te, "determine_exit_code", _capture_exit_code)

    exit_code = _run_executor(ai_runner=_ai_runner)

    assert_that(exit_code).is_equal_to(0)
    assert_that(captured.get("total_issues")).is_equal_to(0)
    assert_that(captured.get("total_remaining")).is_equal_to(0)


def test_no_ai_runner_means_no_ai_and_unchanged_exit_code(monkeypatch, fake_logger):
    """Without an injected runner the executor runs no AI at all."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    import lintro.ai.hook as hook_module

    def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("executor must not construct the AI hook")

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

    monkeypatch.setattr(te, "determine_exit_code", _capture_exit_code)

    exit_code = _run_executor()

    assert_that(exit_code).is_equal_to(1)
    assert_that(captured.get("total_remaining")).is_equal_to(1)


def test_ai_runner_force_failure_overrides_clean_exit_code(monkeypatch, fake_logger):
    """A forcing AI outcome turns a clean run into exit code 1."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _ai_runner(*, all_results: list[ToolResult], **_kwargs: Any) -> AIOutcome:
        _fix_results_in_place(all_results)
        return AIOutcome(ran=True, force_failure=True)

    monkeypatch.setattr(te, "determine_exit_code", lambda **_kwargs: 0)

    exit_code = _run_executor(ai_runner=_ai_runner)

    assert_that(exit_code).is_equal_to(1)


def test_ai_runner_without_force_failure_leaves_exit_code(monkeypatch, fake_logger):
    """A non-forcing AI outcome does not change the computed exit code."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _ai_runner(*, all_results: list[ToolResult], **_kwargs: Any) -> AIOutcome:
        _fix_results_in_place(all_results)
        return AIOutcome(ran=True, force_failure=False)

    monkeypatch.setattr(te, "determine_exit_code", lambda **_kwargs: 0)

    exit_code = _run_executor(ai_runner=_ai_runner)

    assert_that(exit_code).is_equal_to(0)


def test_ai_runner_exception_propagates(monkeypatch, fake_logger):
    """Errors raised by the AI seam propagate, as ``fail_on_ai_error`` needs."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _ai_runner(**_kwargs: Any) -> AIOutcome:
        raise RuntimeError("provider exploded")

    assert_that(_run_executor).raises(RuntimeError).when_called_with(
        ai_runner=_ai_runner,
    )


def test_fail_under_still_forces_failure_after_ai_clears_run(
    monkeypatch,
    fake_logger,
):
    """The score gate can still fail a run that AI left at exit code 0."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)

    def _ai_runner(*, all_results: list[ToolResult], **_kwargs: Any) -> AIOutcome:
        _fix_results_in_place(all_results)
        return AIOutcome(ran=True, force_failure=False)

    monkeypatch.setattr(te, "determine_exit_code", lambda **_kwargs: 0)

    exit_code = _run_executor(fail_under=101.0, ai_runner=_ai_runner)

    assert_that(exit_code).is_equal_to(1)


def test_ai_status_renderer_lines_reach_the_summary(monkeypatch, fake_logger):
    """The injected status renderer supplies the summary's AI rows."""
    lintro_config = _ai_enabled_config()
    _install_executor_doubles(monkeypatch, fake_logger, lintro_config)
    monkeypatch.setattr(te, "determine_exit_code", lambda **_kwargs: 0)

    captured: dict[str, Any] = {}

    import lintro.utils.console.pre_execution_summary as summary_module

    def _fake_summary(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        summary_module,
        "print_pre_execution_summary",
        _fake_summary,
    )

    def _renderer(*, ai_config: Any, is_ci: bool) -> list[str]:
        captured["renderer_ai_config"] = ai_config
        return ["[green]enabled[/green]"]

    exit_code = _run_executor(
        output_format="grid",
        ai_status_renderer=_renderer,
    )

    assert_that(exit_code).is_equal_to(0)
    assert_that(captured.get("ai_status_lines")).is_equal_to(["[green]enabled[/green]"])
    assert_that(captured.get("renderer_ai_config")).is_same_as(lintro_config.ai)
