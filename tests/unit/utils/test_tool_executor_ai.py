"""Tests for AI-specific behavior in tool executor."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.ai.config import AIConfig
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.execution.tool_configuration import ToolsToRunResult
from lintro.utils.tool_executor import _warn_ai_fix_disabled, run_lint_tools_simple


class TestWarnAIFixDisabled:
    """Tests for warning behavior when AI fix is requested but disabled."""

    def test_warns_only_for_check_when_fix_requested_and_ai_disabled(self):
        """Warn when action is CHECK, ai_fix=True, and AI disabled."""
        logger = MagicMock()

        _warn_ai_fix_disabled(
            action=Action.CHECK,
            ai_fix=True,
            ai_enabled=False,
            logger=logger,
        )

        assert_that(logger.console_output.call_count).is_equal_to(1)

    def test_no_warning_for_other_states(self):
        """Test that no warning is issued for non-qualifying state combinations."""
        logger = MagicMock()

        _warn_ai_fix_disabled(
            action=Action.FIX,
            ai_fix=True,
            ai_enabled=False,
            logger=logger,
        )
        _warn_ai_fix_disabled(
            action=Action.CHECK,
            ai_fix=False,
            ai_enabled=False,
            logger=logger,
        )
        _warn_ai_fix_disabled(
            action=Action.CHECK,
            ai_fix=True,
            ai_enabled=True,
            logger=logger,
        )

        assert_that(logger.console_output.call_count).is_equal_to(0)


class TestToolExecutorAITotals:
    """Tests for post-AI total recalculation."""

    def test_fix_recomputes_totals_after_ai_changes(
        self,
        monkeypatch,
        fake_logger,
    ):
        """Test that fix recomputes totals after AI changes."""

        class _FakeTool:
            def set_options(self, **kwargs: Any) -> None:
                return None

            def fix(self, paths: Any, options: Any) -> ToolResult:
                return ToolResult(
                    name="ruff",
                    success=False,
                    issues_count=1,
                    fixed_issues_count=0,
                    remaining_issues_count=1,
                    issues=[],
                )

            def check(self, paths: Any, options: Any) -> ToolResult:
                return ToolResult(
                    name="ruff",
                    success=False,
                    issues_count=1,
                    issues=[],
                )

        lintro_config = LintroConfig(
            execution=ExecutionConfig(parallel=False),
            ai=AIConfig(
                enabled=True,
                auto_apply=True,
            ),
        )

        monkeypatch.setattr(
            te,
            "get_tools_to_run",
            lambda tools, action: ToolsToRunResult(to_run=["ruff"]),
        )
        monkeypatch.setattr(
            te.tool_manager,  # type: ignore[attr-defined]  # singleton
            "get_tool",
            lambda name: _FakeTool(),
        )
        monkeypatch.setattr(
            te,
            "configure_tool_for_execution",
            lambda **kwargs: None,
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

        def _fake_ai_enhancement(**kwargs):
            result = kwargs["all_results"][0]
            result.success = True
            result.issues_count = 0
            result.fixed_issues_count = 0
            result.remaining_issues_count = 0

        import lintro.ai.hook as hook_module

        class _FakeHook:
            def should_run(self, action: Any) -> bool:
                return True

            def execute(
                self,
                action: Any,
                all_results: Any,
                *,
                console_logger: Any,
                output_format: Any,
            ) -> None:
                _fake_ai_enhancement(all_results=all_results)

        monkeypatch.setattr(
            hook_module,
            "AIPostExecutionHook",
            lambda lintro_config, ai_fix=False: _FakeHook(),
        )

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

        exit_code = run_lint_tools_simple(
            action="fmt",
            paths=["."],
            tools="ruff",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="auto",
            output_format="json",
            verbose=False,
            raw_output=False,
        )

        assert_that(exit_code).is_equal_to(0)
        assert_that(captured.get("total_issues")).is_equal_to(0)
        assert_that(captured.get("total_remaining")).is_equal_to(0)
