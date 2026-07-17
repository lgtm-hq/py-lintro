"""Tests for the ``lintro doctor`` CLI command."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that
from click.testing import CliRunner
from loguru import logger

from lintro.ai.config import AIConfig
from lintro.cli_utils.commands.doctor import (
    _generate_markdown_report,
    _output_json,
    doctor_command,
)
from lintro.config.lintro_config import LintroConfig
from lintro.enums.install_context import InstallContext, PackageManager
from lintro.enums.tool_status import ToolStatus
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.tool_registry import ManifestTool
from lintro.utils.doctor_report import ToolCheckResult

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


def _ok_snapshots() -> dict[str, Any]:
    """Return a probe_all_tools result for a healthy ruff install."""
    from lintro.tools.core.snapshots import ToolCapabilities, ToolSnapshot

    return {
        "ruff": ToolSnapshot(
            name="ruff",
            available=True,
            version="0.14.4",
            capabilities=ToolCapabilities(can_fix=True),
            binary_path="/usr/bin/ruff",
            binary_mtime=1.0,
            version_check_passed=True,
            min_version="0.14.0",
        ),
    }


def _missing_snapshots() -> dict[str, Any]:
    """Return a probe_all_tools result for a missing ruff binary."""
    from lintro.tools.core.snapshots import ToolCapabilities, ToolSnapshot

    return {
        "ruff": ToolSnapshot(
            name="ruff",
            available=False,
            version=None,
            capabilities=ToolCapabilities(),
            probe_error="ruff not found in PATH",
            remediation_hint="Install ruff",
            binary_path="",
            binary_mtime=0.0,
            version_check_passed=False,
            min_version="0.14.0",
        ),
    }


def test_doctor_all_ok_exit_0() -> None:
    """Exit code 0 when all tools pass."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch(
            "lintro.tools.core.snapshots.probe_all_tools",
            return_value=_ok_snapshots(),
        ),
    ):
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(0)


def test_doctor_missing_tool_exit_1() -> None:
    """Exit code 1 when a tool is missing."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch(
            "lintro.tools.core.snapshots.probe_all_tools",
            return_value=_missing_snapshots(),
        ),
    ):
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(1)


def test_doctor_json_output_valid() -> None:
    """--json produces valid JSON."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch(
            "lintro.tools.core.snapshots.probe_all_tools",
            return_value=_ok_snapshots(),
        ),
        patch(
            "lintro.cli_utils.commands.doctor.collect_full_environment",
            return_value=None,
        ),
    ):
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
        patch(
            "lintro.tools.core.snapshots.probe_all_tools",
            return_value=_ok_snapshots(),
        ),
        patch(
            "lintro.cli_utils.commands.doctor.collect_full_environment",
            return_value=MagicMock(),
        ),
    ):
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
        patch(
            "lintro.tools.core.snapshots.probe_all_tools",
            return_value=_ok_snapshots(),
        ),
    ):
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


def test_doctor_resolves_ai_checks_from_a_raw_ai_mapping() -> None:
    """Doctor parses the raw ``ai:`` mapping before running AI checks.

    Issue #724 PR 3 made ``LintroConfig.ai`` a plain dict, so ``doctor`` now
    resolves it through the AI facade. This pins that the checks still receive
    a typed configuration and that a typo'd key is reported to the user.
    """
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()
    lintro_config = LintroConfig(
        ai={"enabled": True, "lint": True, "provdier": "anthropic"},
    )
    received: list[Any] = []

    def _record(config: Any) -> list[Any]:
        """Record the AI config the doctor command resolved.

        Args:
            config: The configuration passed to the AI checks.

        Returns:
            An empty list of AI check results.
        """
        received.append(config)
        return []

    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="WARNING",
    )

    try:
        with (
            p1,
            p2,
            patch(
                "lintro.config.config_loader.get_config",
                return_value=lintro_config,
            ),
            patch(
                "lintro.cli_utils.commands.doctor.check_ai_configuration",
                side_effect=_record,
            ),
            patch("subprocess.run") as mock_run,
            patch("shutil.which", return_value="/usr/bin/ruff"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="ruff 0.14.4",
                stderr="",
            )
            result = runner.invoke(doctor_command, [])
    finally:
        logger.remove(handler_id)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(received).is_length(1)
    assert_that(received[0]).is_instance_of(AIConfig)
    assert_that(received[0].lint_enabled).is_true()
    assert_that("".join(messages)).contains(
        "Unknown AI config keys ignored: provdier",
    )


def test_output_json_includes_optional_extras() -> None:
    """JSON output carries the optional-extras block for machine consumers."""
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
    extras = {entry["name"]: entry for entry in data["optional_extras"]}
    assert_that(extras).contains_key("mcp")
