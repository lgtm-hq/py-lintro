"""End-to-end tests for the introspection MCP tools.

Every test drives a real :class:`mcp.client.Client` over in-memory streams
against the same ``Server`` object the stdio transport serves, so the payloads
asserted here are the bytes an agent receives.

The binary probes are stubbed rather than run: this suite is about what the
tools *report*, and probing forty external binaries per test would trade
seconds of runtime for an assertion about the developer's machine. The probes
themselves are covered in ``tests/unit/utils/test_doctor_report.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from assertpy import assert_that
from mcp.client import Client
from mcp.types import Tool

from lintro.config.lintro_config import LintroConfig
from lintro.enums.tool_status import ToolStatus
from lintro.mcp.registry import DEFAULT_TOOL_TIMEOUT_SECONDS
from lintro.mcp.toolkits.introspection import (
    INTROSPECTION_TIMEOUT_SECONDS,
    build_introspection_toolkit,
)
from lintro.tools.core.tool_registry import ManifestTool
from lintro.tools.core.version_parsing import ToolVersionInfo
from lintro.utils import doctor_report
from lintro.utils.doctor_report import ToolCheckResult
from tests.unit.mcp.session_helpers import payload_from_result, run_in_memory_client

_T = TypeVar("_T")


def _run_session(
    *,
    workspace: Path,
    check: Callable[[Client], Awaitable[_T]],
) -> _T:
    """Run ``check`` against a connected in-memory MCP client.

    Args:
        workspace: Workspace root for the server under test.
        check: Async callback receiving an initialized client.

    Returns:
        Whatever ``check`` returns.
    """
    return run_in_memory_client(workspace=workspace, check=check)


def _payload(result: Any) -> dict[str, Any]:
    """Extract a tool result payload as a dict.

    Args:
        result: The ``CallToolResult`` returned by ``client.call_tool``.

    Returns:
        The payload the server sent.
    """
    return payload_from_result(result)


def _call(*, workspace: Path, tool: str) -> dict[str, Any]:
    """Call one argument-less introspection tool and decode its payload.

    Args:
        workspace: Workspace root for the server under test.
        tool: Tool name to call.

    Returns:
        The decoded payload.
    """

    async def _check(session: Client) -> dict[str, Any]:
        result = await session.call_tool(name=tool, arguments={})
        return _payload(result)

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


@pytest.fixture
def stub_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the doctor report's config and AI inputs.

    The workspace session anchors cwd at a throwaway directory, but config
    discovery searches upward from there, so an inherited ``.lintro-config.yaml``
    could add consistency warnings or enable AI checks that report missing
    credentials — and turn a healthy report degraded on someone else's machine.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.config.config_loader.get_config",
        lambda reload=False: LintroConfig(),
    )
    monkeypatch.setattr(
        "lintro.ai.doctor_checks.check_ai_configuration",
        lambda _config: [],
    )


def test_introspection_tools_are_advertised_as_read_only_and_idempotent(
    workspace: Path,
) -> None:
    """The annotations tell a host these three calls are always safe to make."""

    async def _check(session: Client) -> dict[str, Tool]:
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
        assert_that(annotations.read_only_hint).is_true()
        assert_that(annotations.destructive_hint).is_false()
        assert_that(annotations.idempotent_hint).is_true()


def test_introspection_tools_budget_for_probing_every_binary() -> None:
    """The default 300s budget is short of a full manifest of slow probes."""
    specs = build_introspection_toolkit(workspace=Path.cwd())

    assert_that([spec.name for spec in specs]).is_length(3)
    for spec in specs:
        assert_that(spec.timeout_seconds).is_equal_to(INTROSPECTION_TIMEOUT_SECONDS)
        assert_that(spec.timeout_seconds).is_greater_than(
            DEFAULT_TOOL_TIMEOUT_SECONDS,
        )


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
    assert_that(ruff["status"]).is_equal_to("ok")
    assert_that(ruff["origin"]).is_equal_to("builtin")
    assert_that(ruff["version"]).is_equal_to(ruff["expected_version"])
    assert_that(ruff["minimum_version"]).is_not_none()


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
    """A version below the minimum is reported as data, not as a failure."""
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
            "yamllint": ToolVersionInfo(
                name="yamllint",
                min_version="1.40.0",
                recommended_version="1.42.0",
                install_hint="uv pip install yamllint",
                current_version="1.41.0",
                version_check_passed=True,
                below_recommended=True,
            ),
        },
    )

    payload = _call(workspace=workspace, tool="lintro_versions")

    entries = {entry["name"]: entry for entry in payload["tools"]}
    assert_that(entries["ruff"]["status"]).is_equal_to("incompatible")
    assert_that(entries["ruff"]["installed_version"]).is_equal_to("0.9.0")
    assert_that(entries["ruff"]["minimum_version"]).is_equal_to("0.15.9")
    assert_that(entries["ruff"]["satisfies_minimum"]).is_false()
    assert_that(entries["hadolint"]["status"]).is_equal_to("missing")
    assert_that(entries["hadolint"]["installed_version"]).is_none()
    assert_that(entries["hadolint"]["install_hint"]).is_equal_to(
        "brew install hadolint",
    )
    assert_that(entries["black"]["status"]).is_equal_to("ok")
    # Clears the minimum but trails the recommended version: the milder band,
    # and the same label lintro_list_tools would report for it.
    assert_that(entries["yamllint"]["status"]).is_equal_to("outdated")
    assert_that(entries["yamllint"]["satisfies_minimum"]).is_true()
    assert_that(entries["ruff"]).contains_key("binary_path", "advisory")
    assert_that(entries["black"]["advisory"]).is_none()
    assert_that(payload["summary"]).is_equal_to(
        {"incompatible": 1, "missing": 1, "ok": 1, "outdated": 1, "total": 4},
    )


@pytest.mark.usefixtures("stub_environment")
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
            "category",
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
