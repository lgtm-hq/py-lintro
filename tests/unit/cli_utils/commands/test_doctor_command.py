"""Tests for the ``lintro doctor`` CLI command."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.doctor import (
    ToolCheckResult,
    _check_tool,
    _compare_versions,
    _generate_markdown_report,
    _output_json,
    doctor_command,
)
from lintro.enums.install_context import InstallContext, PackageManager
from lintro.enums.tool_status import ToolStatus
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.tool_registry import ManifestTool

# ── Helpers ──────────────────────────────────────────────────────────


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
    """Build a ManifestTool for testing."""
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
    """Build a RuntimeContext for testing."""
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


# ── _compare_versions ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("installed", "expected", "want"),
    [
        ("1.2.3", "1.0.0", ToolStatus.OK),
        ("1.0.0", "1.0.0", ToolStatus.OK),
        ("1.0.0", "1.2.0", ToolStatus.OUTDATED),
        ("0.14.0", "0.15.0", ToolStatus.OUTDATED),
        ("0.0.1", "2.0.0", ToolStatus.INCOMPATIBLE),
        ("invalid", "1.0.0", ToolStatus.UNKNOWN),
    ],
    ids=["above", "equal", "below", "minor_below", "incompatible", "invalid"],
)
def test_compare_versions(installed: str, expected: str, want: ToolStatus) -> None:
    """Compare two version strings and return the correct ToolStatus."""
    minimum = expected if want == ToolStatus.INCOMPATIBLE else "0.0.0"
    assert_that(_compare_versions(installed, expected, minimum)).is_equal_to(want)


# ── _check_tool ──────────────────────────────────────────────────────


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
        result = _check_tool(tool, ctx)

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
        result = _check_tool(tool, ctx)

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
        result = _check_tool(tool, ctx)

    assert_that(result.status).is_equal_to(ToolStatus.INCOMPATIBLE)


def test_check_tool_missing_not_in_path() -> None:
    """Tool executable not found in PATH."""
    tool = _make_tool()
    ctx = _make_context()

    with patch("shutil.which", return_value=None):
        result = _check_tool(tool, ctx)

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
        result = _check_tool(tool, ctx)

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
        result = _check_tool(tool, ctx)

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
        result = _check_tool(tool, ctx)

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
        result = _check_tool(tool, ctx)

    assert_that(result.status).is_equal_to(ToolStatus.UNKNOWN)
    assert_that(result.error).is_equal_to("no_version")


def test_check_tool_no_version_command() -> None:
    """Tool has no version_command defined."""
    tool = _make_tool(version_command=())
    ctx = _make_context()

    result = _check_tool(tool, ctx)

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
        result = _check_tool(tool, ctx)

    assert_that(result.install_hint).is_not_empty()
    assert_that(result.upgrade_hint).is_not_empty()


# ── _output_json ─────────────────────────────────────────────────────


def test_output_json_produces_valid_json() -> None:
    """JSON output is valid and contains expected top-level keys."""
    tool = _make_tool()
    result = ToolCheckResult(
        tool=tool,
        status=ToolStatus.OK,
        installed_version="0.14.4",
        install_hint="uv pip install ruff>=0.14.0",
        upgrade_hint="uv pip install --upgrade ruff>=0.14.0",
    )
    ctx = _make_context()

    from io import StringIO

    output = StringIO()
    with patch("click.echo", side_effect=output.write):
        _output_json([result], ctx, None, 1, 0, 0, 0, 0)

    data = json.loads(output.getvalue())
    assert_that(data).contains_key("context", "tools", "issues", "summary")
    assert_that(data["summary"]["ok"]).is_equal_to(1)


def test_output_json_includes_unknown_in_issues() -> None:
    """Unknown production tools appear in the issues list."""
    tool = _make_tool()
    result = ToolCheckResult(
        tool=tool,
        status=ToolStatus.UNKNOWN,
        error="no_version",
        install_hint="uv pip install ruff>=0.14.0",
        upgrade_hint="uv pip install --upgrade ruff>=0.14.0",
    )
    ctx = _make_context()

    from io import StringIO

    output = StringIO()
    with patch("click.echo", side_effect=output.write):
        _output_json([result], ctx, None, 0, 0, 0, 0, 1)

    data = json.loads(output.getvalue())
    assert_that(data["issues"]).is_length(1)
    assert_that(data["issues"][0]["tool"]).is_equal_to("ruff")


# ── _generate_markdown_report ────────────────────────────────────────


def test_markdown_report_contains_headers() -> None:
    """Markdown report includes Environment and Tool Versions sections."""
    env = MagicMock()
    env.lintro.version = "0.58.2"
    env.system.platform_name = "macOS"
    env.system.architecture = "arm64"
    env.python.version = "3.13.0"
    env.node = None
    env.rust = None

    ctx = _make_context()
    tool = _make_tool()
    results_by_cat = {
        "bundled": [
            ToolCheckResult(
                tool=tool,
                status=ToolStatus.OK,
                installed_version="0.14.4",
            ),
        ],
    }

    md = _generate_markdown_report(env, ctx, results_by_cat, [])
    assert_that(md).contains("### Environment")
    assert_that(md).contains("### Tool Versions")
    assert_that(md).contains("ruff")


# ── CLI invocation ───────────────────────────────────────────────────


def _patch_doctor_deps() -> tuple[Any, Any]:
    """Patch ManifestRegistry.load and RuntimeContext.detect for CLI tests.

    Returns:
        Tuple of two context-manager patches.
    """
    tool = _make_tool()
    registry = MagicMock()
    registry.all_tools = MagicMock(return_value=[tool])
    registry.__contains__ = lambda self, name: name == "ruff"
    registry.get.return_value = tool
    ctx = _make_context()

    return (
        patch(
            "lintro.cli_utils.commands.doctor.ManifestRegistry.load",
            return_value=registry,
        ),
        patch(
            "lintro.cli_utils.commands.doctor.RuntimeContext.detect",
            return_value=ctx,
        ),
    )


def test_doctor_all_ok_exit_0() -> None:
    """Exit code 0 when all tools pass."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(0)


def test_doctor_missing_tool_exit_1() -> None:
    """Exit code 1 when a tool is missing."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with p1, p2, patch("shutil.which", return_value=None):
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(1)


def test_doctor_json_output_valid() -> None:
    """--json produces valid JSON."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch(
            "lintro.cli_utils.commands.doctor.collect_full_environment",
            return_value=None,
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, ["--json"])

    data = json.loads(result.output)
    assert_that(data).contains_key("tools", "summary")


def test_doctor_fix_incompatible_with_json() -> None:
    """--fix --json raises a usage error."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch(
            "lintro.cli_utils.commands.doctor.collect_full_environment",
            return_value=MagicMock(),
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, ["--fix", "--json"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("--fix cannot be combined")


def test_doctor_tools_filter_known_tool() -> None:
    """--tools with a known tool name succeeds."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, ["--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("ruff")


def test_doctor_oxlint_type_aware_failure_exit_1() -> None:
    """A failing oxlint type-aware check causes exit 1 and shows the hint."""
    from lintro.tools.definitions.oxlint_doctor import OxlintCheckResult

    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    failing = [
        OxlintCheckResult(
            name="oxlint.type-aware.tsgolint",
            status=ToolStatus.MISSING,
            message="oxlint-tsgolint not resolvable (node_modules / bunx)",
            hint="bun add -d oxlint-tsgolint@latest",
        ),
    ]

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch(
            "lintro.cli_utils.commands.doctor.check_oxlint_type_aware",
            return_value=failing,
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Oxlint type-aware")
    assert_that(result.output).contains("bun add -d oxlint-tsgolint@latest")


def test_doctor_unknown_tool_name_exit_1() -> None:
    """--tools with unknown name prints error and exits 1."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with p1, p2:
        result = runner.invoke(doctor_command, ["--tools", "nonexistent"])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Unknown tools")
