"""Tests for the ``lintro install`` CLI command."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.install import install_command
from lintro.enums.install_context import InstallContext, PackageManager
from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_strategies import InstallEnvironment
from lintro.tools.core.tool_installer import InstallPlan, InstallResult
from lintro.tools.core.tool_registry import ManifestTool

# ── Helpers ──────────────────────────────────────────────────────────


def _make_tool(name: str = "ruff", version: str = "0.14.0") -> ManifestTool:
    """Build a ManifestTool for testing."""
    return ManifestTool(
        name=name,
        version=version,
        min_version=version,
        install_type="pip",
        tier="tools",
        category="bundled",
        version_command=(name, "--version"),
    )


def _mock_registry() -> MagicMock:
    """Build a mock ManifestRegistry."""
    registry = MagicMock()
    registry.profile_names = [
        "minimal",
        "recommended",
        "complete",
        "ci",
        "full",
        "python",
        "web",
    ]
    registry.__contains__ = lambda self, name: name in ("ruff", "mypy")
    registry.all_tools.return_value = [_make_tool("ruff"), _make_tool("mypy")]
    registry.get.side_effect = _make_tool
    registry.tools_for_profile.return_value = [_make_tool("ruff")]
    return registry


def _runtime_context() -> RuntimeContext:
    """Build a real RuntimeContext with no enclosing Node project.

    A ``MagicMock`` will not do here: the command reports which Node package
    manager it selected and why (#2005), which reads real enum-typed fields.

    Returns:
        A RuntimeContext describing a bun+npm machine outside a Node project.
    """
    return RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset({PackageManager.BUN, PackageManager.NPM}),
        ),
        is_ci=False,
    )


def _patches() -> tuple[Any, Any]:
    """Common patches for install CLI tests."""
    registry = _mock_registry()
    return (
        patch(
            "lintro.cli_utils.commands.install.ManifestRegistry.load",
            return_value=registry,
        ),
        patch(
            "lintro.cli_utils.commands.install.RuntimeContext.detect",
            return_value=_runtime_context(),
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
            InstallResult(
                tool=tool,
                outcome=InstallOutcome.SUCCESS,
                message="OK",
                duration_seconds=1.0,
            ),
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
    """--all resolves to the 'full' profile."""
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
    # Verify the plan was called with profile="full"
    assert_that(
        mock_cls.return_value.plan.call_args.kwargs["profile"],
    ).is_equal_to("full")


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
            InstallResult(
                tool=tool,
                outcome=InstallOutcome.FAILED,
                message="Command failed",
            ),
        ]
        result = runner.invoke(install_command, ["ruff"])

    assert_that(result.exit_code).is_equal_to(1)


@pytest.mark.parametrize(
    "outcome",
    [
        InstallOutcome.NOT_DISCOVERABLE,
        InstallOutcome.STILL_OUTDATED,
    ],
)
def test_install_non_success_outcomes_exit_1(outcome: InstallOutcome) -> None:
    """NOT_DISCOVERABLE and STILL_OUTDATED keep the exit-1-on-any-non-success contract.

    Args:
        outcome: Non-success outcome that must still fail the process.
    """
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
            InstallResult(
                tool=tool,
                outcome=outcome,
                message=str(outcome),
            ),
        ]
        result = runner.invoke(install_command, ["ruff"])

    assert_that(result.exit_code).is_equal_to(1)


def test_install_help_documents_exit_1_on_non_success() -> None:
    """CLI help states that NOT_DISCOVERABLE still exits 1."""
    runner = CliRunner()
    result = runner.invoke(install_command, ["--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Exits 1")
    assert_that(result.output).contains("NOT_DISCOVERABLE")
    assert_that(result.output).does_not_contain("Args:")


def test_detect_languages_returns_list() -> None:
    """_detect_languages returns a list without raising."""
    from lintro.cli_utils.commands.install import _detect_languages

    result = _detect_languages()
    assert_that(result).is_instance_of(list)


# ── Node package-manager flags (#2005) ───────────────────────────────


def test_node_package_manager_flag_reaches_the_runtime_context() -> None:
    """--node-package-manager is mapped to a PackageManager and passed through."""
    runner = CliRunner()
    registry_patch, _context_patch = _patches()

    with (
        registry_patch,
        patch(
            "lintro.cli_utils.commands.install.RuntimeContext.detect",
            return_value=_runtime_context(),
        ) as mock_detect,
        patch("lintro.cli_utils.commands.install.ToolInstaller") as mock_cls,
    ):
        mock_cls.return_value.plan.return_value = InstallPlan(already_ok=[_make_tool()])
        result = runner.invoke(
            install_command,
            ["ruff", "--node-package-manager", "pnpm"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_detect.call_args.kwargs).is_equal_to(
        {"node_package_manager": PackageManager.PNPM, "prefer_global": False},
    )


def test_global_flag_reaches_the_runtime_context() -> None:
    """--global is passed through as prefer_global."""
    runner = CliRunner()
    registry_patch, _context_patch = _patches()

    with (
        registry_patch,
        patch(
            "lintro.cli_utils.commands.install.RuntimeContext.detect",
            return_value=_runtime_context(),
        ) as mock_detect,
        patch("lintro.cli_utils.commands.install.ToolInstaller") as mock_cls,
    ):
        mock_cls.return_value.plan.return_value = InstallPlan(already_ok=[_make_tool()])
        result = runner.invoke(install_command, ["ruff", "--global"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_detect.call_args.kwargs).is_equal_to(
        {"node_package_manager": None, "prefer_global": True},
    )


def test_unknown_node_package_manager_is_rejected() -> None:
    """An unsupported manager name fails before anything is planned."""
    runner = CliRunner()
    registry_patch, context_patch = _patches()

    with registry_patch, context_patch:
        result = runner.invoke(
            install_command,
            ["ruff", "--node-package-manager", "corn"],
        )

    assert_that(result.exit_code).is_not_equal_to(0)


def test_selected_node_manager_is_reported_inside_a_project(tmp_path: Path) -> None:
    """The chosen manager and its evidence are printed, not left implicit.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    (tmp_path / "package.json").write_text('{"name": "demo"}', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("", encoding="utf-8")
    runner = CliRunner()
    registry_patch, _context_patch = _patches()
    context = RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment.detect(InstallContext.PIP, start=tmp_path),
        is_ci=False,
    )

    with (
        registry_patch,
        patch(
            "lintro.cli_utils.commands.install.RuntimeContext.detect",
            return_value=context,
        ),
        patch("lintro.cli_utils.commands.install.ToolInstaller") as mock_cls,
    ):
        mock_cls.return_value.plan.return_value = InstallPlan(already_ok=[_make_tool()])
        result = runner.invoke(install_command, ["ruff"])

    assert_that(result.output).contains("npm", "lockfile", "project dev dependency")
