"""Tests for the ``lintro install`` CLI command."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.install import install_command
from lintro.tools.core.tool_installer import InstallPlan, InstallResult
from lintro.tools.core.tool_registry import ManifestTool

# ── Helpers ──────────────────────────────────────────────────────────


def _make_tool(name: str = "ruff", version: str = "0.14.0") -> ManifestTool:
    """Build a ManifestTool for testing."""
    return ManifestTool(
        name=name,
        version=version,
        install_type="pip",
        tier="tools",
        category="bundled",
        version_command=(name, "--version"),
    )


def _mock_registry() -> MagicMock:
    """Build a mock ToolRegistry."""
    registry = MagicMock()
    registry.profile_names = ["minimal", "recommended", "complete", "ci"]
    registry.__contains__ = lambda self, name: name in ("ruff", "mypy")
    registry.all_tools.return_value = [_make_tool("ruff"), _make_tool("mypy")]
    registry.get.side_effect = _make_tool
    registry.tools_for_profile.return_value = [_make_tool("ruff")]
    return registry


def _patches() -> tuple[Any, Any]:
    """Common patches for install CLI tests."""
    registry = _mock_registry()
    return (
        patch(
            "lintro.cli_utils.commands.install.ToolRegistry.load",
            return_value=registry,
        ),
        patch(
            "lintro.cli_utils.commands.install.RuntimeContext.detect",
            return_value=MagicMock(),
        ),
    )


# ── CLI invocation ───────────────────────────────────────────────────


def test_install_all_already_installed() -> None:
    """Exit 0 when all tools are already installed."""
    runner = CliRunner()
    p1, p2 = _patches()

    plan = InstallPlan(already_ok=[_make_tool()])
    with (
        p1,
        p2,
        patch(
            "lintro.cli_utils.commands.install.ToolInstaller",
        ) as mock_cls,
        patch("lintro.cli_utils.commands.install._detect_languages", return_value=[]),
    ):
        mock_cls.return_value.plan.return_value = plan
        result = runner.invoke(install_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("already installed")


def test_install_specific_tools() -> None:
    """Install specific tool names passed as positional args."""
    runner = CliRunner()
    p1, p2 = _patches()

    tool = _make_tool()
    plan = InstallPlan(to_install=[(tool, "pip install ruff>=0.14.0")])
    with (
        p1,
        p2,
        patch(
            "lintro.cli_utils.commands.install.ToolInstaller",
        ) as mock_cls,
    ):
        mock_cls.return_value.plan.return_value = plan
        mock_cls.return_value.execute.return_value = [
            InstallResult(tool=tool, success=True, message="OK", duration_seconds=1.0),
        ]
        result = runner.invoke(install_command, ["ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("ruff")


def test_install_dry_run() -> None:
    """--dry-run shows plan without executing."""
    runner = CliRunner()
    p1, p2 = _patches()

    tool = _make_tool()
    plan = InstallPlan(to_install=[(tool, "pip install ruff>=0.14.0")])
    with (
        p1,
        p2,
        patch(
            "lintro.cli_utils.commands.install.ToolInstaller",
        ) as mock_cls,
    ):
        mock_cls.return_value.plan.return_value = plan
        result = runner.invoke(install_command, ["ruff", "--dry-run"])

        assert_that(result.exit_code).is_equal_to(0)
        assert_that(result.output).contains("Dry run")
        # execute should NOT have been called
        mock_cls.return_value.execute.assert_not_called()


def test_install_conflicting_selectors() -> None:
    """Tools + --profile raises UsageError."""
    runner = CliRunner()
    p1, p2 = _patches()

    with p1, p2:
        result = runner.invoke(install_command, ["ruff", "--profile", "minimal"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Cannot combine")


def test_install_unknown_tool_name() -> None:
    """Unknown tool name raises UsageError."""
    runner = CliRunner()
    p1, p2 = _patches()

    with p1, p2:
        result = runner.invoke(install_command, ["nonexistent"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Unknown tools")


def test_install_unknown_profile() -> None:
    """Unknown profile name raises UsageError."""
    runner = CliRunner()
    p1, p2 = _patches()

    with p1, p2:
        result = runner.invoke(install_command, ["--profile", "nonexistent"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Unknown profile")


def test_install_all_flag() -> None:
    """--all resolves to the 'complete' profile."""
    runner = CliRunner()
    p1, p2 = _patches()

    plan = InstallPlan(already_ok=[_make_tool()])
    with (
        p1,
        p2,
        patch(
            "lintro.cli_utils.commands.install.ToolInstaller",
        ) as mock_cls,
    ):
        mock_cls.return_value.plan.return_value = plan
        result = runner.invoke(install_command, ["--all"])

    assert_that(result.exit_code).is_equal_to(0)
    # Verify the plan was called with profile="complete"
    assert_that(
        mock_cls.return_value.plan.call_args.kwargs["profile"],
    ).is_equal_to("complete")


def test_install_failure_exit_1() -> None:
    """Failed installs produce exit code 1."""
    runner = CliRunner()
    p1, p2 = _patches()

    tool = _make_tool()
    plan = InstallPlan(to_install=[(tool, "pip install ruff>=0.14.0")])
    with (
        p1,
        p2,
        patch(
            "lintro.cli_utils.commands.install.ToolInstaller",
        ) as mock_cls,
    ):
        mock_cls.return_value.plan.return_value = plan
        mock_cls.return_value.execute.return_value = [
            InstallResult(tool=tool, success=False, message="Command failed"),
        ]
        result = runner.invoke(install_command, ["ruff"])

    assert_that(result.exit_code).is_equal_to(1)


# ── _detect_languages ────────────────────────────────────────────────


def test_detect_languages_returns_list() -> None:
    """_detect_languages returns a list without raising."""
    from lintro.cli_utils.commands.install import _detect_languages

    result = _detect_languages()
    assert_that(result).is_instance_of(list)
