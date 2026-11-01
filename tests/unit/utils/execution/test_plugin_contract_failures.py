"""Invalid plugin payloads fail one tool without becoming a clean verdict."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest
from assertpy import assert_that

import lintro.utils.execution.parallel_executor as parallel_module
import lintro.utils.tool_executor as sequential_module
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.tools import tool_manager
from lintro.utils.execution.exit_codes import determine_exit_code
from lintro.utils.execution.run_context import RunContext
from lintro.utils.unified_config import UnifiedConfigManager


@dataclass
class _ContractPlugin(BaseToolPlugin):
    """Plugin advertising v2 while still producing an invalid v1 payload."""

    LINTRO_PLUGIN_API_VERSION = 2
    tool_name: str = "invalid-plugin"
    invalid_field: str = ""

    @property
    def definition(self) -> ToolDefinition:
        """Return metadata for the in-memory plugin.

        Returns:
            A fix-capable tool definition.
        """
        return ToolDefinition(
            name=self.tool_name,
            description="Contract test",
            can_fix=True,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Construct the plugin's result, exercising real model validation.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            A valid result only when no invalid field was requested.
        """
        payload: dict[str, Any] = {"name": self.tool_name, "success": True}
        if self.invalid_field == "fixable":
            # Build the v1 property pattern dynamically so static typing does
            # not need an ignore for the intentionally incompatible override.
            legacy_issue = type(
                "LegacyIssue",
                (BaseIssue,),
                {"fixable": property(lambda self: True)},
            )
            payload["issues"] = [legacy_issue()]
        elif self.invalid_field:
            payload[self.invalid_field] = None
        return ToolResult(**payload)

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Exercise the same result contract in fix mode.

        Args:
            paths: Unused scan paths.
            options: Unused runtime options.

        Returns:
            The plugin's result, if construction succeeds.
        """
        return self.check(paths, options)


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "parallel"])
@pytest.mark.parametrize("during_setup", [False, True], ids=["execute", "setup"])
@pytest.mark.parametrize("action", [Action.CHECK, Action.FIX])
@pytest.mark.parametrize(
    "invalid_field",
    ["issues", "initial_issues", "metadata", "fixable"],
)
def test_plugin_contract_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
    parallel: bool,
    during_setup: bool,
    action: Action,
    invalid_field: str,
) -> None:
    """A misdeclared plugin fails with details while another tool completes.

    Args:
        monkeypatch: Pytest patch fixture.
        parallel: Whether to dispatch through real parallel workers.
        during_setup: Whether the plugin constructs the bad payload at setup.
        action: Check or fix execution.
        invalid_field: The v1 payload that violates the v2 contract.
    """
    tools = {
        "invalid-plugin": _ContractPlugin(invalid_field=invalid_field),
        "healthy-plugin": _ContractPlugin(tool_name="healthy-plugin"),
    }
    monkeypatch.setattr(tool_manager, "get_tool", tools.__getitem__)
    monkeypatch.setattr(
        tool_manager,
        "get_parallel_batches",
        lambda tools, **_: [list(tools)],
    )

    def configure(**kwargs: Any) -> BaseToolPlugin:
        tool = cast(BaseToolPlugin, kwargs["tool"])
        if during_setup and kwargs["tool_name"] == "invalid-plugin":
            tool.check([], {})
        return tool

    module = parallel_module if parallel else sequential_module
    monkeypatch.setattr(module, "configure_tool_for_execution", configure)
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
            dry_run_preview=True,
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

    by_name = {result.name: result for result in results}
    assert_that(by_name).contains_only("invalid-plugin", "healthy-plugin")
    assert_that(by_name["healthy-plugin"].success).is_true()
    failure = by_name["invalid-plugin"]
    expected_error = (
        "AttributeError: property 'fixable' of 'LegacyIssue' object has no setter"
        if invalid_field == "fixable"
        else f"TypeError: ToolResult.{invalid_field} must not be None"
    )
    assert_that(failure.output).contains(expected_error)
    assert_that(failure.success).is_false()
    assert_that(failure.skipped).is_false()
    assert_that(failure.no_files).is_false()
    assert_that(failure.inspected_files).is_none()
    assert_that(failure.issues).is_empty()
    assert_that(failure.metadata).is_empty()
    assert_that(failure.duration_seconds).is_greater_than_or_equal_to(0.0)
    assert_that(failure.fixed_issues_count).is_none()
    assert_that(failure.remaining_issues_count).is_none()
    assert_that(failure.residual_unknown).is_equal_to(action == Action.FIX)
    if action == Action.FIX:
        assert_that(failure.residual_unknown_reason).is_equal_to("tool crashed")
    assert_that(determine_exit_code(action, results, 0, 0)).is_equal_to(1)
