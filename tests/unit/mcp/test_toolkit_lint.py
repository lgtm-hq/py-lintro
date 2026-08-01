"""End-to-end tests for the ``lintro_check`` / ``lintro_format`` MCP tools.

Every test drives a real :class:`mcp.ClientSession` over in-memory streams
against the same ``Server`` object the stdio transport serves, and every one
runs the real ``ruff`` binary against a throwaway workspace. Stubbing the
runner would leave the interesting part — that a dry run really does leave the
tree byte-identical — untested.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from assertpy import assert_that
from mcp import ClientSession, types
from mcp.shared.memory import create_connected_server_and_client_session

from lintro.mcp.server import create_mcp_server

_T = TypeVar("_T")

_UNFORMATTED = "import os\nx    =  1\n"
_UNFIXABLE = "undefined_name_here\n"


def _run_session(
    *,
    workspace: Path,
    check: Callable[[ClientSession], Awaitable[_T]],
) -> _T:
    """Run ``check`` against a connected in-memory MCP client session.

    Args:
        workspace: Workspace root for the server under test.
        check: Async callback receiving an initialized client session.

    Returns:
        Whatever ``check`` returns.
    """
    server = create_mcp_server(workspace=workspace)

    async def _main() -> _T:
        async with create_connected_server_and_client_session(server) as session:
            return await check(session)

    return asyncio.run(_main())


def _payload(result: types.CallToolResult) -> dict[str, Any]:
    """Extract a tool result payload as a dict.

    Args:
        result: The ``CallToolResult`` returned by ``session.call_tool``.

    Returns:
        The payload the server sent.
    """
    if result.structuredContent:
        return dict(result.structuredContent)
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return dict(json.loads(block.text))


def _call(
    *,
    workspace: Path,
    tool: str,
    arguments: dict[str, Any],
) -> tuple[types.CallToolResult, dict[str, Any]]:
    """Call one MCP tool and return its raw result and decoded payload.

    Args:
        workspace: Workspace root for the server under test.
        tool: Tool name to call.
        arguments: Tool arguments.

    Returns:
        The ``CallToolResult`` and its payload.
    """

    async def _check(
        session: ClientSession,
    ) -> tuple[types.CallToolResult, dict[str, Any]]:
        result = await session.call_tool(tool, arguments)
        return result, _payload(result)

    return _run_session(workspace=workspace, check=_check)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a throwaway Python workspace with one unformatted module.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path: The resolved workspace root.
    """
    (tmp_path / "bad.py").write_text(_UNFORMATTED, encoding="utf-8")
    return tmp_path.resolve()


def test_lint_tools_are_listed_with_their_annotations(workspace: Path) -> None:
    """Both tools are advertised with the safety hints their contract implies."""

    async def _check(session: ClientSession) -> dict[str, types.Tool]:
        listed = await session.list_tools()
        return {tool.name: tool for tool in listed.tools}

    tools = _run_session(workspace=workspace, check=_check)

    assert_that(tools).contains_key("lintro_check", "lintro_format")

    check_hints = tools["lintro_check"].annotations
    assert check_hints is not None
    assert_that(check_hints.readOnlyHint).is_true()
    assert_that(check_hints.destructiveHint).is_false()
    assert_that(check_hints.idempotentHint).is_true()

    format_hints = tools["lintro_format"].annotations
    assert format_hints is not None
    assert_that(format_hints.readOnlyHint).is_false()
    assert_that(format_hints.destructiveHint).is_true()
    assert_that(format_hints.idempotentHint).is_false()


def test_check_returns_structured_findings_and_tool_summary(workspace: Path) -> None:
    """lintro_check reports findings and a per-tool summary, changing nothing."""
    before = (workspace / "bad.py").read_bytes()

    result, payload = _call(
        workspace=workspace,
        tool="lintro_check",
        arguments={"tools": ["ruff"]},
    )

    assert_that(result.isError).is_false()
    assert_that(payload["findings"]).is_not_empty()
    finding = payload["findings"][0]
    assert_that(finding).contains_key(
        "tool",
        "file",
        "line",
        "column",
        "rule",
        "severity",
        "message",
        "fixable",
    )
    assert_that(finding["tool"]).is_equal_to("ruff")

    summary = payload["tools"][0]
    assert_that(summary["tool"]).is_equal_to("ruff")
    assert_that(summary["status"]).is_equal_to("issues")
    assert_that(summary["issue_count"]).is_greater_than(0)
    assert_that(summary["duration"]).is_greater_than_or_equal_to(0.0)
    assert_that(payload["summary"]["success"]).is_false()
    assert_that((workspace / "bad.py").read_bytes()).is_equal_to(before)


def test_check_on_a_clean_workspace_returns_no_findings(tmp_path: Path) -> None:
    """A workspace with nothing to report yields an empty findings array."""
    (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")

    result, payload = _call(
        workspace=tmp_path.resolve(),
        tool="lintro_check",
        arguments={"tools": ["ruff"]},
    )

    assert_that(result.isError).is_false()
    assert_that(payload["findings"]).is_empty()
    assert_that(payload["summary"]["total_findings"]).is_equal_to(0)
    assert_that(payload["tools"][0]["status"]).is_equal_to("passed")
    assert_that(payload["summary"]["success"]).is_true()


def test_check_respects_the_tools_filter(workspace: Path) -> None:
    """Only the requested tool appears in the per-tool summary."""
    _result, payload = _call(
        workspace=workspace,
        tool="lintro_check",
        arguments={"tools": ["ruff"]},
    )

    assert_that([summary["tool"] for summary in payload["tools"]]).is_equal_to(["ruff"])


def test_check_rejects_an_unknown_tool_with_tool_unavailable(workspace: Path) -> None:
    """An unregistered tool name is refused rather than silently ignored."""
    result, payload = _call(
        workspace=workspace,
        tool="lintro_check",
        arguments={"tools": ["not-a-real-linter"]},
    )

    assert_that(result.isError).is_true()
    envelope = payload["error"]
    assert_that(envelope["code"]).is_equal_to("tool_unavailable")
    assert_that(envelope["detail"]["tools"]).is_equal_to(["not-a-real-linter"])


def test_check_rejects_an_advisory_tool(workspace: Path) -> None:
    """Advisory AI finders belong to ``lintro review``, not to chk/fmt."""
    result, payload = _call(
        workspace=workspace,
        tool="lintro_check",
        arguments={"tools": ["idiom-review"]},
    )

    assert_that(result.isError).is_true()
    assert_that(payload["error"]["code"]).is_equal_to("tool_unavailable")
    assert_that(payload["error"]["message"]).contains("advisory")


def test_check_rejects_a_tool_the_workspace_config_disabled(tmp_path: Path) -> None:
    """A requested tool the config turned off is refused, not silently skipped."""
    workspace = tmp_path.resolve()
    (workspace / "bad.py").write_text(_UNFORMATTED, encoding="utf-8")
    (workspace / ".lintro-config.yaml").write_text(
        "tools:\n  ruff:\n    enabled: false\n",
        encoding="utf-8",
    )

    result, payload = _call(
        workspace=workspace,
        tool="lintro_check",
        arguments={"tools": ["ruff"]},
    )

    assert_that(result.isError).is_true()
    envelope = payload["error"]
    assert_that(envelope["code"]).is_equal_to("tool_unavailable")
    assert_that(envelope["detail"]["skipped"]).contains_key("ruff")


def test_check_rejects_a_path_outside_the_workspace(workspace: Path) -> None:
    """A path argument escaping the workspace is refused before dispatch."""
    result, payload = _call(
        workspace=workspace,
        tool="lintro_check",
        arguments={"paths": ["../elsewhere"], "tools": ["ruff"]},
    )

    assert_that(result.isError).is_true()
    assert_that(payload["error"]["code"]).is_equal_to("workspace_violation")


def test_check_rejects_arguments_that_violate_the_schema(workspace: Path) -> None:
    """Unknown properties are rejected by the tool's own JSON Schema."""
    result, payload = _call(
        workspace=workspace,
        tool="lintro_check",
        arguments={"nonsense": True},
    )

    assert_that(result.isError).is_true()
    assert_that(payload["error"]["code"]).is_equal_to("invalid_input")


def test_format_dry_run_returns_diffs_and_leaves_the_tree_untouched(
    workspace: Path,
) -> None:
    """The default dry run reports a real diff without writing anything."""
    before = (workspace / "bad.py").read_bytes()

    result, payload = _call(
        workspace=workspace,
        tool="lintro_format",
        arguments={"tools": ["ruff"]},
    )

    assert_that(result.isError).is_false()
    assert_that(payload["dry_run"]).is_true()
    assert_that(payload["changed_files"]).is_equal_to(["bad.py"])

    diff = payload["diffs"][0]
    assert_that(diff["file"]).is_equal_to("bad.py")
    assert_that(diff["diff"]).contains("--- a/bad.py", "+++ b/bad.py", "-import os")
    assert_that((workspace / "bad.py").read_bytes()).is_equal_to(before)


def test_format_apply_writes_the_changes_and_reports_them(workspace: Path) -> None:
    """dry_run=false applies the formatting and still returns the diffs."""
    before = (workspace / "bad.py").read_bytes()

    result, payload = _call(
        workspace=workspace,
        tool="lintro_format",
        arguments={"tools": ["ruff"], "dry_run": False},
    )

    assert_that(result.isError).is_false()
    assert_that(payload["dry_run"]).is_false()
    assert_that(payload["changed_files"]).is_equal_to(["bad.py"])
    assert_that(payload["diffs"][0]["diff"]).contains("-import os")

    after = (workspace / "bad.py").read_bytes()
    assert_that(after).is_not_equal_to(before)
    assert_that(after.decode("utf-8")).does_not_contain("import os")


def test_format_reports_only_the_findings_it_could_not_fix(tmp_path: Path) -> None:
    """After a fix run the findings array is the residue, not the whole run."""
    workspace = tmp_path.resolve()
    (workspace / "bad.py").write_text(_UNFORMATTED + _UNFIXABLE, encoding="utf-8")

    _result, payload = _call(
        workspace=workspace,
        tool="lintro_format",
        arguments={"tools": ["ruff"]},
    )

    rules = {finding["rule"] for finding in payload["findings"]}
    assert_that(rules).contains("F821")
    assert_that(rules).does_not_contain("F401")
    assert_that(payload["tools"][0]["fixed_count"]).is_greater_than(0)


def test_format_on_a_clean_workspace_reports_no_changes(tmp_path: Path) -> None:
    """Nothing to format means no changed files and no diffs."""
    workspace = tmp_path.resolve()
    (workspace / "good.py").write_text("x = 1\n", encoding="utf-8")

    result, payload = _call(
        workspace=workspace,
        tool="lintro_format",
        arguments={"tools": ["ruff"]},
    )

    assert_that(result.isError).is_false()
    assert_that(payload["changed_files"]).is_empty()
    assert_that(payload["diffs"]).is_empty()


def test_check_does_not_leave_run_logs_in_the_workspace(workspace: Path) -> None:
    """A read-only tool keeps its promise: no .lintro directory is created."""
    result, _payload = _call(
        workspace=workspace,
        tool="lintro_check",
        arguments={"tools": ["ruff"]},
    )

    assert_that(result.isError).is_false()
    assert_that((workspace / ".lintro").exists()).is_false()


def test_a_call_does_not_leak_the_log_directory_override(workspace: Path) -> None:
    """LINTRO_LOG_DIR is restored, so the redirect cannot follow other callers."""
    before = os.environ.get("LINTRO_LOG_DIR")

    _call(workspace=workspace, tool="lintro_check", arguments={"tools": ["ruff"]})

    assert_that(os.environ.get("LINTRO_LOG_DIR")).is_equal_to(before)


def test_a_call_restores_the_working_directory(workspace: Path) -> None:
    """The process cwd is anchored for the run and handed back afterwards."""
    before = Path.cwd()

    _call(workspace=workspace, tool="lintro_check", arguments={"tools": ["ruff"]})

    assert_that(Path.cwd()).is_equal_to(before)


def test_format_rejects_a_tool_that_cannot_format(workspace: Path) -> None:
    """Asking a check-only tool to format is refused, not silently downgraded."""
    result, payload = _call(
        workspace=workspace,
        tool="lintro_format",
        arguments={"tools": ["bandit"]},
    )

    assert_that(result.isError).is_true()
    assert_that(payload["error"]["code"]).is_equal_to("tool_unavailable")
