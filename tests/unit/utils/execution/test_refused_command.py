"""A command lintro refuses to spawn fails that tool's run (#3486).

``validate_subprocess_command`` refuses an argument ``cmd.exe`` would
interpret when argv0 is a ``.cmd``/``.bat`` shim. Whether the plugin catches
the ``ValueError`` and reports a failed verdict, or lets it escape, the tool
must come out as an execution failure with incomplete findings and a reason
naming the argument, never as a clean or ordinary verdict.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest
from assertpy import assert_that

import lintro.utils.execution.parallel_executor as parallel_module
import lintro.utils.tool_executor as sequential_module
from lintro.enums.action import Action
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.tools import tool_manager
from lintro.utils.execution.exit_codes import determine_exit_code
from lintro.utils.execution.run_context import RunContext
from lintro.utils.unified_config import UnifiedConfigManager

#: A Node shim and a repository path holding ``&``, legal on Windows.
_BATCH_COMMAND: list[str] = ["C:/repo/node_modules/.bin/prettier.cmd", "a&whoami&b"]


@dataclass
class _ShimPlugin(BaseToolPlugin):
    """Plugin that runs a batch shim with an argument cmd.exe would interpret."""

    tool_name: str = "shim-plugin"
    catch_error: bool = True
    command: tuple[str, ...] = tuple(_BATCH_COMMAND)

    @property
    def definition(self) -> ToolDefinition:
        """Return metadata for the in-memory plugin.

        Returns:
            A fix-capable tool definition.
        """
        return ToolDefinition(
            name=self.tool_name,
            description="Refused command test",
            can_fix=True,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run the shim the way a plugin's own error handling would.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            A failed verdict carrying the error, as several plugins report a
            launch ``ValueError``, or a pass when the command ran.

        Raises:
            ValueError: The refusal itself, when the plugin does not catch it.
        """
        try:
            success, output = self._run_subprocess(list(self.command))
        except ValueError as exc:
            if not self.catch_error:
                raise
            return ToolResult.from_outcome(
                ToolOutcome(execution=Checked(success=False)),
                name=self.tool_name,
                output=f"Execution failed: {exc}",
            )
        return ToolResult(name=self.tool_name, success=success, output=output)

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run the same command in fix mode.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            The check result.
        """
        return self.check(paths, options)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tools: dict[str, BaseToolPlugin],
    parallel: bool,
    action: Action,
) -> dict[str, ToolResult]:
    """Run *tools* through the real sequential or parallel executor.

    Args:
        monkeypatch: Pytest patch fixture.
        tools: Plugins by name.
        parallel: Whether to dispatch through parallel workers.
        action: Check or fix execution.

    Returns:
        Results by tool name.
    """
    monkeypatch.setattr(tool_manager, "get_tool", tools.__getitem__)
    monkeypatch.setattr(
        tool_manager,
        "get_parallel_batches",
        lambda tools, **_: [list(tools)],
    )
    module = parallel_module if parallel else sequential_module
    monkeypatch.setattr(
        module,
        "configure_tool_for_execution",
        lambda **kwargs: cast(BaseToolPlugin, kwargs["tool"]).copy_for_execution(),
    )
    arguments: dict[str, Any] = {
        "tools_to_run": list(tools),
        "paths": ["."],
        "config_manager": cast(UnifiedConfigManager, object()),
        "tool_option_dict": {},
        "exclude": None,
        "include_venv": False,
        "selected_tools": set(tools),
    }
    if parallel:
        results = parallel_module.run_tools_parallel(
            **arguments,
            action=action,
            max_workers=2,
        )
    else:
        ctx = RunContext(
            action=action,
            selection_action=action,
            dry_run_preview=False,
            output_manager=None,
            logger=SimpleNamespace(
                print_tool_header=lambda **_: None,
                console_output=lambda _: None,
            ),
            lintro_config=SimpleNamespace(execution=SimpleNamespace(max_fix_retries=1)),
            clean_stdout_output=True,
        )
        results = sequential_module._execute_tools_sequential(
            **arguments,
            ctx=ctx,
            incremental=False,
            effective_auto_install=False,
            diff_base=None,
            on_tool_result=None,
        )
    return {result.name: result for result in results}


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "parallel"])
@pytest.mark.parametrize("catch_error", [True, False], ids=["caught", "escaped"])
@pytest.mark.parametrize("action", [Action.CHECK, Action.FIX])
def test_refused_batch_argument_is_an_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
    parallel: bool,
    catch_error: bool,
    action: Action,
) -> None:
    """The refused tool fails with a named reason; another tool still passes.

    Args:
        monkeypatch: Pytest patch fixture.
        parallel: Whether to dispatch through parallel workers.
        catch_error: Whether the plugin turns the error into its own verdict.
        action: Check or fix execution.
    """
    results = _run(
        monkeypatch,
        tools={
            "shim-plugin": _ShimPlugin(catch_error=catch_error),
            "healthy-plugin": _ShimPlugin(
                tool_name="healthy-plugin",
                command=(sys.executable, "-c", "pass"),
            ),
        },
        parallel=parallel,
        action=action,
    )

    assert_that(results["healthy-plugin"].execution_failed).is_false()
    refused = results["shim-plugin"]
    assert_that(refused.execution_failed).is_true()
    assert_that(refused.findings_complete).is_false()
    assert_that(refused.success).is_false()
    assert_that(refused.inspected_files).is_none()
    assert_that(refused.inspected_paths).is_none()
    assert_that(refused.issues).is_empty()
    assert_that(refused.output).starts_with("lintro refused to run shim-plugin: ")
    assert_that(refused.output).contains(
        "argument 1 ('a&whoami&b') to batch file 'prettier.cmd'",
    )
    assert_that(refused.output).contains("cmd.exe would interpret '&' in it")
    assert_that(refused.residual_unknown).is_equal_to(action == Action.FIX)
    assert_that(determine_exit_code(action, list(results.values()), 0, 0)).is_equal_to(
        1,
    )


def test_copy_for_execution_does_not_inherit_a_refusal() -> None:
    """A refusal on the registry template does not mark later runs."""
    template = _ShimPlugin()
    template.check([], {})

    fresh = template.copy_for_execution()

    assert_that(template.command_refusal).is_not_none()
    assert_that(fresh.command_refusal).is_none()
