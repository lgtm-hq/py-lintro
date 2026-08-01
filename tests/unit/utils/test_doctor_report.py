"""Tests for the print-free doctor data layer.

Covers both halves of :mod:`lintro.utils.doctor_report`: the binary probes the
``lintro doctor`` CLI has always used, and the ``{check, status, detail,
remediation}`` health report the MCP ``lintro_doctor`` tool serves.
"""

from __future__ import annotations

import subprocess  # nosec B404 - only used to build the TimeoutExpired the probe must survive
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.config.lintro_config import LintroConfig
from lintro.enums.install_context import InstallContext, PackageManager
from lintro.enums.tool_status import ToolStatus
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.tool_registry import ManifestRegistry, ManifestTool
from lintro.utils import doctor_report
from lintro.utils.doctor_report import (
    DoctorCheck,
    DoctorCheckCategory,
    DoctorCheckStatus,
    DoctorHealth,
    DoctorReport,
    ToolCheckResult,
    check_tool,
    collect_doctor_report,
    collect_tool_checks,
    mcp_extra_status,
    tool_status_for_versions,
)


def _make_tool(
    name: str = "ruff",
    version: str = "0.14.0",
    min_version: str | None = None,
    *,
    install_type: str = "pip",
    tier: str = "tools",
    category: str = "bundled",
    version_command: tuple[str, ...] | None = None,
) -> ManifestTool:
    """Build a ManifestTool for testing.

    Args:
        name: Tool name.
        version: Recommended version from the manifest.
        min_version: Minimum compatible version; defaults to ``version``.
        install_type: Manifest install type.
        tier: Manifest tier ("tools" or "dev").
        category: Display category.
        version_command: Probe command; defaults to ``(name, "--version")``.

    Returns:
        ManifestTool: The constructed entry.
    """
    return ManifestTool(
        name=name,
        version=version,
        min_version=min_version or version,
        install_type=install_type,
        tier=tier,
        category=category,
        version_command=(
            (name, "--version") if version_command is None else version_command
        ),
        languages=("python",),
        tags=("linter",),
    )


def _make_context(*, has_brew: bool = False) -> RuntimeContext:
    """Build a RuntimeContext for testing.

    Args:
        has_brew: Whether Homebrew is among the available package managers.

    Returns:
        RuntimeContext: The constructed context.
    """
    managers = frozenset(
        {
            PackageManager.UV,
            PackageManager.PIP,
            PackageManager.NPM,
            PackageManager.CARGO,
            PackageManager.RUSTUP,
        },
    )
    if has_brew:
        managers = managers | {PackageManager.BREW}
    return RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=managers,
        ),
        is_ci=False,
    )


def _check(
    *,
    name: str = "check",
    status: DoctorCheckStatus = DoctorCheckStatus.OK,
) -> DoctorCheck:
    """Build a doctor check for testing.

    Args:
        name: Check identifier.
        status: Check status.

    Returns:
        DoctorCheck: The constructed record.
    """
    return DoctorCheck(
        check=name,
        status=status,
        detail="detail",
        remediation="do something",
        category=DoctorCheckCategory.TOOLS,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ToolStatus.OK, True),
        (ToolStatus.OUTDATED, True),
        (ToolStatus.INCOMPATIBLE, True),
        (ToolStatus.UNKNOWN, True),
        (ToolStatus.MISSING, False),
        (ToolStatus.DISABLED, False),
    ],
)
def test_installed_follows_whether_the_binary_can_run(
    status: ToolStatus,
    expected: bool,
) -> None:
    """A tool counts as installed exactly when its binary answered a probe."""
    result = ToolCheckResult(tool=_make_tool(), status=status)

    assert_that(result.installed).is_equal_to(expected)


@pytest.mark.parametrize(
    ("installed", "recommended", "minimum", "expected"),
    [
        ("1.2.3", "1.2.3", "1.0.0", ToolStatus.OK),
        ("2.0.0", "1.2.3", "1.0.0", ToolStatus.OK),
        ("1.1.0", "1.2.3", "1.0.0", ToolStatus.OUTDATED),
        ("0.14.0", "0.15.0", "0.0.0", ToolStatus.OUTDATED),
        ("0.9.0", "1.2.3", "1.0.0", ToolStatus.INCOMPATIBLE),
        ("0.0.1", "2.0.0", "2.0.0", ToolStatus.INCOMPATIBLE),
        ("not-a-version", "1.2.3", "1.0.0", ToolStatus.UNKNOWN),
    ],
    ids=[
        "equal",
        "above",
        "below_recommended",
        "minor_below_recommended",
        "below_minimum",
        "far_below_minimum",
        "unparseable",
    ],
)
def test_version_comparison_classifies_each_band(
    installed: str,
    recommended: str,
    minimum: str,
    expected: ToolStatus,
) -> None:
    """Installed versions map onto the manifest's minimum/recommended bands."""
    status = tool_status_for_versions(
        installed=installed,
        recommended=recommended,
        minimum=minimum,
    )

    assert_that(status).is_equal_to(expected)


def test_report_is_healthy_when_no_check_warns_or_errors() -> None:
    """Skipped checks describe inapplicable features, not degradation."""
    report = DoctorReport(
        checks=(
            _check(name="a", status=DoctorCheckStatus.OK),
            _check(name="b", status=DoctorCheckStatus.SKIPPED),
        ),
    )

    assert_that(report.health).is_equal_to(DoctorHealth.HEALTHY)
    assert_that(report.summary()).is_equal_to(
        {"ok": 1, "warning": 0, "error": 0, "skipped": 1, "total": 2},
    )


@pytest.mark.parametrize(
    "status",
    [DoctorCheckStatus.WARNING, DoctorCheckStatus.ERROR],
)
def test_report_is_degraded_when_any_check_is_actionable(
    status: DoctorCheckStatus,
) -> None:
    """One warning is enough to stop calling the environment healthy."""
    report = DoctorReport(
        checks=(_check(status=DoctorCheckStatus.OK), _check(status=status)),
    )

    assert_that(report.health).is_equal_to(DoctorHealth.DEGRADED)


def test_check_serializes_the_four_contract_fields() -> None:
    """The wire shape an agent branches on stays ``{check, status, detail, ...}``."""
    payload = _check(name="tools.missing", status=DoctorCheckStatus.ERROR).to_dict()

    assert_that(payload).contains_key(
        "check",
        "status",
        "detail",
        "remediation",
        "category",
    )
    assert_that(payload["status"]).is_equal_to("error")


def test_disabled_tools_are_reported_rather_than_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool turned off in config must be distinguishable from a missing one."""
    registry = ManifestRegistry(
        tools={"ruff": _make_tool("ruff"), "black": _make_tool("black")},
        language_map={},
        profiles={},
    )
    config = LintroConfig()
    monkeypatch.setattr(
        type(config),
        "is_tool_enabled",
        lambda _self, name: name == "ruff",
    )
    monkeypatch.setattr(
        doctor_report,
        "check_tool",
        lambda *, tool, context: ToolCheckResult(tool=tool, status=ToolStatus.OK),
    )

    results = collect_tool_checks(
        registry=registry,
        context=RuntimeContext.detect(),
        config=config,
    )

    by_name = {result.tool.name: result.status for result in results}
    assert_that(by_name).is_equal_to(
        {"ruff": ToolStatus.OK, "black": ToolStatus.DISABLED},
    )


def test_named_tools_are_probed_even_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator naming a tool wants it probed, config enablement aside."""
    registry = ManifestRegistry(
        tools={"ruff": _make_tool("ruff"), "black": _make_tool("black")},
        language_map={},
        profiles={},
    )
    config = LintroConfig()
    monkeypatch.setattr(type(config), "is_tool_enabled", lambda _self, name: False)
    monkeypatch.setattr(
        doctor_report,
        "check_tool",
        lambda *, tool, context: ToolCheckResult(tool=tool, status=ToolStatus.OK),
    )

    results = collect_tool_checks(
        registry=registry,
        context=RuntimeContext.detect(),
        config=config,
        tool_names=["black"],
    )

    assert_that([result.tool.name for result in results]).is_equal_to(["black"])
    assert_that(results[0].status).is_equal_to(ToolStatus.OK)


@pytest.fixture
def stub_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the report's inputs so a developer's own setup cannot change them.

    ``collect_doctor_report`` reads the workspace config and probes the AI
    provider; without this the assertions below would depend on whether the
    machine running them happens to have AI enabled.

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


def test_unloadable_config_is_reported_as_a_check_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed config file must degrade the report, never crash it."""

    def _explode(reload: bool = False) -> LintroConfig:
        raise ValueError("mapping values are not allowed here")

    monkeypatch.setattr("lintro.config.config_loader.get_config", _explode)
    monkeypatch.setattr(
        doctor_report,
        "collect_tool_checks",
        lambda **_kwargs: [ToolCheckResult(tool=_make_tool(), status=ToolStatus.OK)],
    )

    report = collect_doctor_report()

    checks = {check.check: check for check in report.checks}
    assert_that(report.health).is_equal_to(DoctorHealth.DEGRADED)
    assert_that(checks["config.load"].status).is_equal_to(DoctorCheckStatus.ERROR)
    assert_that(checks["config.load"].detail).contains("mapping values")
    assert_that(checks["config.load"].remediation).is_not_empty()
    # Nothing downstream of the config may claim to have checked anything.
    assert_that(checks["ai.provider"].status).is_equal_to(DoctorCheckStatus.SKIPPED)


@pytest.mark.usefixtures("stub_environment")
def test_missing_binaries_are_folded_into_one_actionable_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tools verdict names what is missing and how to install it."""
    missing = ToolCheckResult(tool=_make_tool("hadolint"), status=ToolStatus.MISSING)
    outdated = ToolCheckResult(
        tool=_make_tool("ruff"),
        status=ToolStatus.OUTDATED,
        installed_version="1.1.0",
    )
    ignored = ToolCheckResult(
        tool=_make_tool("pytest", tier="dev"),
        status=ToolStatus.MISSING,
    )
    monkeypatch.setattr(
        doctor_report,
        "collect_tool_checks",
        lambda **_kwargs: [missing, outdated, ignored],
    )

    report = collect_doctor_report()

    checks = {check.check: check for check in report.checks}
    assert_that(checks["tools.missing"].status).is_equal_to(DoctorCheckStatus.ERROR)
    assert_that(checks["tools.missing"].detail).contains("hadolint")
    assert_that(checks["tools.missing"].detail).does_not_contain("pytest")
    assert_that(checks["tools.missing"].remediation).is_equal_to(
        "lintro install hadolint",
    )
    assert_that(checks["tools.versions"].status).is_equal_to(DoctorCheckStatus.WARNING)
    assert_that(checks["tools.versions"].detail).contains("ruff")


@pytest.mark.usefixtures("stub_environment")
def test_healthy_tools_still_emit_their_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passing check is reported too, so a caller never guesses from absence."""
    monkeypatch.setattr(
        doctor_report,
        "collect_tool_checks",
        lambda **_kwargs: [ToolCheckResult(tool=_make_tool(), status=ToolStatus.OK)],
    )

    report = collect_doctor_report()

    checks = {check.check: check.status for check in report.checks}
    assert_that(checks["tools.missing"]).is_equal_to(DoctorCheckStatus.OK)
    assert_that(checks["tools.versions"]).is_equal_to(DoctorCheckStatus.OK)


@pytest.mark.usefixtures("stub_environment")
def test_mcp_extra_is_never_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An uninstalled optional extra is a fact about the install, not a fault."""
    monkeypatch.setattr("lintro.mcp.is_mcp_available", lambda: False)
    monkeypatch.setattr(
        doctor_report,
        "collect_tool_checks",
        lambda **_kwargs: [ToolCheckResult(tool=_make_tool(), status=ToolStatus.OK)],
    )

    report = collect_doctor_report()

    extras = [check for check in report.checks if check.check == "extras.mcp"]
    assert_that(extras).is_length(1)
    assert_that(extras[0].status).is_equal_to(DoctorCheckStatus.SKIPPED)
    assert_that(extras[0].remediation).contains("lintro[mcp]")


@pytest.mark.usefixtures("stub_environment")
def test_ai_checks_are_projected_onto_the_doctor_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing AI credential is an error an operator can act on."""
    from lintro.ai.doctor_checks import AICheckResult

    monkeypatch.setattr(
        doctor_report,
        "collect_tool_checks",
        lambda **_kwargs: [ToolCheckResult(tool=_make_tool(), status=ToolStatus.OK)],
    )
    monkeypatch.setattr(
        "lintro.ai.doctor_checks.check_ai_configuration",
        lambda _config: [
            AICheckResult(
                name="ai.api.ANTHROPIC_API_KEY",
                status=ToolStatus.MISSING,
                message="Environment variable ANTHROPIC_API_KEY is not set",
                hint="Export ANTHROPIC_API_KEY",
            ),
        ],
    )

    report = collect_doctor_report()

    checks: dict[str, Any] = {check.check: check for check in report.checks}
    assert_that(checks).contains_key("ai.api.ANTHROPIC_API_KEY")
    assert_that(checks["ai.api.ANTHROPIC_API_KEY"].status).is_equal_to(
        DoctorCheckStatus.ERROR,
    )
    assert_that(checks["ai.api.ANTHROPIC_API_KEY"].remediation).is_equal_to(
        "Export ANTHROPIC_API_KEY",
    )
    assert_that(report.health).is_equal_to(DoctorHealth.DEGRADED)


# ── check_tool ───────────────────────────────────────────────────────


def test_check_tool_ok() -> None:
    """Tool found in PATH with version meeting minimum."""
    tool = _make_tool(version="0.14.0")
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.14.4",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OK)
    assert_that(result.installed_version).is_equal_to("0.14.4")
    assert_that(result.path).is_equal_to("/usr/bin/ruff")


def test_check_tool_outdated() -> None:
    """Tool found but version below recommended."""
    tool = _make_tool(version="1.0.0", min_version="0.3.0")
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.5.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert_that(result.installed_version).is_equal_to("0.5.0")


def test_check_tool_incompatible() -> None:
    """Tool found but version below hard minimum."""
    tool = _make_tool(version="1.0.0", min_version="1.0.0")
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.5.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.INCOMPATIBLE)


def test_check_tool_missing_not_in_path() -> None:
    """Tool executable not found in PATH."""
    tool = _make_tool()
    ctx = _make_context()

    with patch("shutil.which", return_value=None):
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.MISSING)
    assert_that(result.error).is_equal_to("not_in_path")


def test_check_tool_missing_command_failed() -> None:
    """Tool found but version command exits non-zero."""
    tool = _make_tool()
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.MISSING)
    assert_that(result.error).is_equal_to("command_failed")


def test_check_tool_missing_timeout() -> None:
    """Tool version command times out."""
    tool = _make_tool()
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ruff"], timeout=10),
        ),
    ):
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.MISSING)
    assert_that(result.error).is_equal_to("timeout")


def test_check_tool_missing_os_error() -> None:
    """Tool version command raises OSError."""
    tool = _make_tool()
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run", side_effect=OSError("exec format error")),
    ):
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.MISSING)
    assert_that(result.error).is_equal_to("os_error")


def test_check_tool_unknown_no_version() -> None:
    """Tool runs but output has no parseable version."""
    tool = _make_tool()
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="no version here",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.UNKNOWN)
    assert_that(result.error).is_equal_to("no_version")


def test_check_tool_no_version_command() -> None:
    """Tool has no version_command defined."""
    tool = _make_tool(version_command=())
    ctx = _make_context()

    result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.MISSING)
    assert_that(result.error).is_equal_to("no_command")


def test_check_tool_upgrade_hint_populated() -> None:
    """Both install_hint and upgrade_hint are populated."""
    tool = _make_tool()
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.14.4",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.install_hint).is_not_empty()
    assert_that(result.upgrade_hint).is_not_empty()


# ── optional MCP extra ───────────────────────────────────────────────


def test_mcp_extra_status_reports_installed() -> None:
    """The MCP extra is reported OK when the SDK imports."""
    with patch("lintro.mcp.is_mcp_available", return_value=True):
        info = mcp_extra_status()

    assert_that(info["name"]).is_equal_to("mcp")
    assert_that(info["status"]).is_equal_to(ToolStatus.OK.value)
    assert_that(info["hint"]).contains("lintro mcp")


def test_mcp_extra_status_reports_missing_without_failing() -> None:
    """A missing MCP extra is DISABLED (informational), never an error."""
    with patch("lintro.mcp.is_mcp_available", return_value=False):
        info = mcp_extra_status()

    assert_that(info["status"]).is_equal_to(ToolStatus.DISABLED.value)
    assert_that(info["hint"]).contains("lintro[mcp]")
