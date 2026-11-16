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

import lintro.utils.execution.advisory as advisory_module
import lintro.utils.execution.parallel_executor as parallel_module
import lintro.utils.tool_executor as sequential_module
from lintro.ai.review import lint_bridge
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.exceptions.errors import UnsafeCommandError
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.tools import tool_manager
from lintro.utils.execution.exit_codes import determine_exit_code
from lintro.utils.execution.result_shaping import (
    mark_refused_command,
    refused_command_failure,
)
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


def test_refusal_keeps_output_that_already_echoes_it() -> None:
    """Real findings text survives when the plugin also echoed the refusal."""
    refusal = UnsafeCommandError("argument 1 ('a&b') would be interpreted")
    tool = SimpleNamespace(command_refusal=refusal)
    finding = "src/ok.ts:3:1 TS2322 Type 'string' is not assignable"
    result = ToolResult(
        name="tsc",
        success=False,
        output=f"{finding}\nChunk 2 failed: {refusal}",
    )

    marked = mark_refused_command(tool, result, fix=False)

    assert_that(marked.output).starts_with(f"lintro refused to run tsc: {refusal}")
    assert_that(marked.output).contains(finding)
    assert_that(marked.execution_failed).is_true()


def _copy_configured(**kwargs: object) -> BaseToolPlugin:
    """Stand in for configuration: a fresh per-run copy, as the real one makes.

    Args:
        **kwargs: ``configure_tool_for_execution`` keyword arguments.

    Returns:
        A per-run copy of the template tool.
    """
    return cast(BaseToolPlugin, kwargs["tool"]).copy_for_execution()


def _run_lint_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tools: dict[str, BaseToolPlugin],
) -> list[ToolResult]:
    """Run *tools* through the AI review lint bridge.

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
    monkeypatch.setattr(lint_bridge, "configure_tool_for_execution", _copy_configured)
    return lint_bridge.run_lint_on_changed_files(
        changed_files=["src/app.ts"],
        lintro_config=LintroConfig(),
    )


def _run_advisory(
    monkeypatch: pytest.MonkeyPatch,
    tools: dict[str, BaseToolPlugin],
) -> list[ToolResult]:
    """Run *tools* through the advisory runner used by ``lintro review``.

    Args:
        monkeypatch: Pytest patch fixture.
        tools: Plugins by name.

    Returns:
        The runner's results.
    """
    monkeypatch.setattr(advisory_module, "UnifiedConfigManager", lambda: object())
    monkeypatch.setattr(tool_manager, "get_tool", tools.__getitem__)
    monkeypatch.setattr(
        advisory_module,
        "configure_tool_for_execution",
        _copy_configured,
    )
    return advisory_module.run_advisory_tools(
        paths=["src"],
        tool_names=list(tools),
        lintro_config=LintroConfig(),
    )


@pytest.mark.parametrize(
    "runner",
    [_run_lint_bridge, _run_advisory],
    ids=["lint-bridge", "advisory"],
)
@pytest.mark.parametrize("catch_error", [True, False], ids=["caught", "escaped"])
def test_direct_callers_report_a_refused_command_as_an_execution_failure(
    monkeypatch: pytest.MonkeyPatch,
    runner: Any,
    catch_error: bool,
) -> None:
    """Callers outside the executors report a refusal the same way (#3493).

    Args:
        monkeypatch: Pytest patch fixture.
        runner: Caller that runs plugins without the tool executor.
        catch_error: Whether the plugin turns the error into its own verdict.
    """
    results = runner(
        monkeypatch,
        {
            "shim-plugin": _ShimPlugin(catch_error=catch_error),
            "healthy-plugin": _ShimPlugin(
                tool_name="healthy-plugin",
                command=(sys.executable, "-c", "pass"),
            ),
        },
    )

    by_name = {result.name: result for result in results}
    assert_that(by_name).contains_key("shim-plugin", "healthy-plugin")
    assert_that(by_name["healthy-plugin"].execution_failed).is_false()
    assert_that(by_name["healthy-plugin"].success).is_true()
    refused = by_name["shim-plugin"]
    assert_that(refused.execution_failed).is_true()
    assert_that(refused.findings_complete).is_false()
    assert_that(refused.success).is_false()
    assert_that(refused.inspected_paths).is_none()
    assert_that(refused.output).starts_with("lintro refused to run shim-plugin: ")
    assert_that(refused.output).contains("cmd.exe would interpret '&' in it")
    assert_that(refused.output.count("lintro refused to run")).is_equal_to(1)


def test_escaped_failure_without_a_refusal_is_not_a_refused_command() -> None:
    """An ordinary exception leaves the caller's own handling in place."""
    tool = _ShimPlugin(command=(sys.executable, "-c", "pass"))
    tool.check([], {})

    assert_that(refused_command_failure(tool, name="shim-plugin", fix=False)).is_none()
    assert_that(refused_command_failure(None, name="shim-plugin", fix=False)).is_none()
