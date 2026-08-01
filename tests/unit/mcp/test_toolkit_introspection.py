"""End-to-end tests for the introspection MCP tools.

Every test drives a real :class:`mcp.ClientSession` over in-memory streams
against the same ``Server`` object the stdio transport serves, so the payloads
asserted here are the bytes an agent receives.

The binary probes are stubbed rather than run: this suite is about what the
tools *report*, and probing forty external binaries per test would trade
seconds of runtime for an assertion about the developer's machine. The probes
themselves are covered in ``tests/unit/utils/test_doctor_report.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from assertpy import assert_that
from mcp import ClientSession, types
from mcp.shared.memory import create_connected_server_and_client_session

from lintro.enums.tool_status import ToolStatus
from lintro.mcp.server import create_mcp_server
from lintro.tools.core.tool_registry import ManifestTool
from lintro.tools.core.version_parsing import ToolVersionInfo
from lintro.utils import doctor_report
from lintro.utils.doctor_report import ToolCheckResult

_T = TypeVar("_T")


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


def _call(*, workspace: Path, tool: str) -> dict[str, Any]:
    """Call one argument-less introspection tool and decode its payload.

    Args:
        workspace: Workspace root for the server under test.
        tool: Tool name to call.

    Returns:
        The decoded payload.
    """

    async def _check(session: ClientSession) -> dict[str, Any]:
        return _payload(await session.call_tool(tool, {}))

    return _run_session(workspace=workspace, check=_check)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a throwaway workspace root.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path: The resolved workspace root.
    """
    return tmp_path.resolve()


@pytest.fixture
def stub_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report every manifest tool as installed and current.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        doctor_report,
        "check_tool",
        lambda *, tool, context: ToolCheckResult(
            tool=tool,
            status=ToolStatus.OK,
            installed_version=tool.version,
            path=f"/usr/local/bin/{tool.name}",
        ),
    )


def test_introspection_tools_are_advertised_as_read_only_and_idempotent(
    workspace: Path,
) -> None:
    """The annotations tell a host these three calls are always safe to make."""

    async def _check(session: ClientSession) -> dict[str, types.Tool]:
        listed = await session.list_tools()
        return {tool.name: tool for tool in listed.tools}

    tools = _run_session(workspace=workspace, check=_check)

    assert_that(tools).contains_key(
        "lintro_list_tools",
        "lintro_versions",
        "lintro_doctor",
    )
    for name in ("lintro_list_tools", "lintro_versions", "lintro_doctor"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert_that(annotations.readOnlyHint).is_true()
        assert_that(annotations.destructiveHint).is_false()
        assert_that(annotations.idempotentHint).is_true()


@pytest.mark.usefixtures("stub_probes")
def test_list_tools_reports_a_complete_entry_for_every_tool(
    workspace: Path,
) -> None:
    """Each entry carries the fields an agent needs to plan a call."""
    payload = _call(workspace=workspace, tool="lintro_list_tools")

    assert_that(payload["tools"]).is_not_empty()
    for entry in payload["tools"]:
        assert_that(entry).contains_key(
            "name",
            "description",
            "types",
            "languages",
            "installed",
            "version",
            "expected_version",
            "can_fix",
            "capabilities",
            "execution_class",
            "profile_membership",
        )
    assert_that(payload["summary"]["total"]).is_equal_to(len(payload["tools"]))


@pytest.mark.usefixtures("stub_probes")
def test_list_tools_describes_ruff_as_a_fixing_deterministic_linter(
    workspace: Path,
) -> None:
    """A tool that is both linter and formatter reports both types."""
    payload = _call(workspace=workspace, tool="lintro_list_tools")

    ruff = next(entry for entry in payload["tools"] if entry["name"] == "ruff")
    assert_that(ruff["types"]).contains("linter", "formatter")
    assert_that(ruff["languages"]).contains("python")
    assert_that(ruff["can_fix"]).is_true()
    assert_that(ruff["capabilities"]).contains("check", "fix")
    assert_that(ruff["execution_class"]).is_equal_to("deterministic")
    assert_that(ruff["installed"]).is_true()


@pytest.mark.usefixtures("stub_probes")
def test_list_tools_marks_advisory_finders_so_they_are_not_called_as_linters(
    workspace: Path,
) -> None:
    """An AI finder runs under ``lintro_review``, never under ``lintro_check``."""
    payload = _call(workspace=workspace, tool="lintro_list_tools")

    finders = [
        entry for entry in payload["tools"] if entry["execution_class"] == "advisory"
    ]
    assert_that(finders).is_not_empty()
    for finder in finders:
        assert_that(finder["capabilities"]).is_equal_to(["review"])


@pytest.mark.usefixtures("stub_probes")
def test_list_tools_reports_static_profile_membership(workspace: Path) -> None:
    """Profiles whose membership is fixed by the manifest are attributed."""
    payload = _call(workspace=workspace, tool="lintro_list_tools")

    ruff = next(entry for entry in payload["tools"] if entry["name"] == "ruff")
    assert_that(ruff["profile_membership"]).contains("minimal", "complete")

    profiles = {profile["name"]: profile for profile in payload["profiles"]}
    assert_that(profiles["complete"]["resolution"]).is_equal_to("static")
    # "recommended" resolves against detected languages, so membership in it is
    # a property of a workspace and is deliberately not claimed per tool.
    assert_that(profiles["recommended"]["resolution"]).is_equal_to("workspace")
    for entry in payload["tools"]:
        assert_that(entry["profile_membership"]).does_not_contain("recommended")


def test_list_tools_keeps_missing_binaries_visible(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool that is not installed is listed with installed=false, not dropped."""

    def _probe(*, tool: ManifestTool, context: Any) -> ToolCheckResult:
        if tool.name == "hadolint":
            return ToolCheckResult(
                tool=tool,
                status=ToolStatus.MISSING,
                error="not_in_path",
                install_hint="brew install hadolint",
            )
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.OK,
            installed_version=tool.version,
        )

    monkeypatch.setattr(doctor_report, "check_tool", _probe)

    payload = _call(workspace=workspace, tool="lintro_list_tools")

    hadolint = next(entry for entry in payload["tools"] if entry["name"] == "hadolint")
    assert_that(hadolint["installed"]).is_false()
    assert_that(hadolint["version"]).is_none()
    assert_that(hadolint["status"]).is_equal_to("missing")
    assert_that(hadolint["install_hint"]).is_equal_to("brew install hadolint")
    assert_that(payload["summary"]["missing"]).is_greater_than_or_equal_to(1)


def test_versions_reports_installed_against_expected(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version below the minimum is reported as outdated, not as a failure."""
    monkeypatch.setattr(
        "lintro.tools.core.version_requirements.get_all_tool_versions",
        lambda: {
            "ruff": ToolVersionInfo(
                name="ruff",
                min_version="0.15.9",
                recommended_version="0.15.9",
                install_hint="uv pip install ruff",
                current_version="0.9.0",
                version_check_passed=False,
                error_message="Version 0.9.0 is below minimum requirement 0.15.9",
            ),
            "hadolint": ToolVersionInfo(
                name="hadolint",
                min_version="2.14.0",
                recommended_version="2.14.0",
                install_hint="brew install hadolint",
                error_message="Command failed: hadolint --version",
            ),
            "black": ToolVersionInfo(
                name="black",
                min_version="26.1.0",
                recommended_version="26.1.0",
                install_hint="uv pip install black",
                current_version="26.1.0",
                version_check_passed=True,
            ),
        },
    )

    payload = _call(workspace=workspace, tool="lintro_versions")

    entries = {entry["name"]: entry for entry in payload["tools"]}
    assert_that(entries["ruff"]["status"]).is_equal_to("outdated")
    assert_that(entries["ruff"]["installed_version"]).is_equal_to("0.9.0")
    assert_that(entries["ruff"]["minimum_version"]).is_equal_to("0.15.9")
    assert_that(entries["ruff"]["satisfies_minimum"]).is_false()
    assert_that(entries["hadolint"]["status"]).is_equal_to("missing")
    assert_that(entries["hadolint"]["installed_version"]).is_none()
    assert_that(entries["hadolint"]["install_hint"]).is_equal_to(
        "brew install hadolint",
    )
    assert_that(entries["black"]["status"]).is_equal_to("ok")
    assert_that(payload["summary"]).is_equal_to(
        {"outdated": 1, "missing": 1, "ok": 1, "total": 3},
    )


def test_doctor_reports_a_healthy_environment(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With everything in place the report is healthy and still lists its checks."""
    monkeypatch.setattr(
        doctor_report,
        "collect_tool_checks",
        lambda **_kwargs: [
            ToolCheckResult(
                tool=ManifestTool(
                    name="ruff",
                    version="1.0.0",
                    min_version="1.0.0",
                    install_type="pip",
                    tier="tools",
                    category="bundled",
                    version_command=("ruff", "--version"),
                ),
                status=ToolStatus.OK,
                installed_version="1.0.0",
            ),
        ],
    )

    payload = _call(workspace=workspace, tool="lintro_doctor")

    assert_that(payload["health"]).is_equal_to("healthy")
    checks = {check["check"]: check for check in payload["checks"]}
    assert_that(checks).contains_key(
        "config.load",
        "config.consistency",
        "tools.missing",
        "tools.versions",
        "extras.mcp",
    )
    for check in payload["checks"]:
        assert_that(check).contains_key(
            "check",
            "status",
            "detail",
            "remediation",
        )
    assert_that(payload["summary"]["error"]).is_equal_to(0)


def test_doctor_reports_a_degraded_environment_with_remediation(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing binary degrades the report and names the command that fixes it."""
    monkeypatch.setattr(
        doctor_report,
        "collect_tool_checks",
        lambda **_kwargs: [
            ToolCheckResult(
                tool=ManifestTool(
                    name="hadolint",
                    version="2.14.0",
                    min_version="2.14.0",
                    install_type="binary",
                    tier="tools",
                    category="external",
                    version_command=("hadolint", "--version"),
                ),
                status=ToolStatus.MISSING,
                error="not_in_path",
                install_hint="brew install hadolint",
            ),
        ],
    )

    payload = _call(workspace=workspace, tool="lintro_doctor")

    assert_that(payload["health"]).is_equal_to("degraded")
    missing = next(
        check for check in payload["checks"] if check["check"] == "tools.missing"
    )
    assert_that(missing["status"]).is_equal_to("error")
    assert_that(missing["detail"]).contains("hadolint")
    assert_that(missing["remediation"]).is_equal_to("lintro install hadolint")
    assert_that(payload["summary"]["error"]).is_greater_than_or_equal_to(1)
