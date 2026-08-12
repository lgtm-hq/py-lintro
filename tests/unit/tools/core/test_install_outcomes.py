"""Tests for install outcome classification and batch continuation."""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired is referenced; no process is spawned
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.cli_utils.install_output import unresolved_tool_names
from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_plan import InstallPlan
from lintro.tools.core.tool_installer import ToolInstaller
from lintro.tools.core.tool_registry import ManifestRegistry, ManifestTool


def _installer() -> ToolInstaller:
    """Build an installer bound to a pip install context.

    Returns:
        ToolInstaller instance.
    """
    return ToolInstaller(ManifestRegistry.load(), RuntimeContext.detect())


def _tool(name: str) -> ManifestTool:
    """Build a minimal pip-installed manifest tool.

    Args:
        name: Tool name.

    Returns:
        ManifestTool instance.
    """
    return ManifestTool(
        name=name,
        version="1.2.0",
        min_version="1.2.0",
        install_type="pip",
        install_package=name,
        version_command=(name, "--version"),
    )


def test_command_success_and_discoverable_is_success() -> None:
    """A zero exit plus a discoverable binary is a full success."""
    installer = _installer()
    tool = _tool("ruff")

    with (
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
        patch(
            "lintro.tools.core.tool_installer.shutil.which",
            return_value="/usr/local/bin/ruff",
        ),
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="ruff 1.2.0", stderr=""),
        ]
        result = installer._run_install(tool, "pip install ruff")

    assert_that(result.outcome).is_equal_to(InstallOutcome.SUCCESS)


def test_command_success_without_discovery_is_flagged() -> None:
    """A zero exit with no discoverable binary is reported distinctly."""
    installer = _installer()
    tool = _tool("ruff")

    with (
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
        patch("lintro.tools.core.tool_installer.shutil.which", return_value=None),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = installer._run_install(tool, "pip install ruff")

    assert_that(result.outcome).is_equal_to(InstallOutcome.NOT_DISCOVERABLE)
    assert_that(result.success).is_false()
    assert_that(result.message).contains("not discoverable")
    # A rerun installs the same package to the same place — it cannot fix PATH.
    assert_that(result.outcome.is_retryable).is_false()


def test_non_zero_exit_is_failed() -> None:
    """A non-zero exit is classified as FAILED, not a timeout."""
    installer = _installer()

    with patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr='No available formula with the name "golangci_lint"',
        )
        result = installer._run_install(
            _tool("golangci_lint"),
            "brew install golangci_lint",
        )

    assert_that(result.outcome).is_equal_to(InstallOutcome.FAILED)
    assert_that(result.outcome.is_retryable).is_false()


def test_timeout_is_classified_separately_from_failure() -> None:
    """A timeout keeps its own outcome so it can be retried."""
    installer = _installer()

    with patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="brew", timeout=300)
        result = installer._run_install(_tool("clippy"), "brew install clippy")

    assert_that(result.outcome).is_equal_to(InstallOutcome.TIMED_OUT)
    assert_that(result.outcome.is_retryable).is_true()


def test_unparseable_version_output_still_counts_as_installed() -> None:
    """A discoverable binary is a success even if its version is unparseable."""
    installer = _installer()
    tool = _tool("faketool")

    with (
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
        patch(
            "lintro.tools.core.tool_installer.shutil.which",
            return_value="/usr/local/bin/faketool",
        ),
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="faketool 1.2.0", stderr=""),
        ]
        result = installer._run_install(tool, "pip install faketool")

    assert_that(result.outcome).is_equal_to(InstallOutcome.SUCCESS)


def test_wrapper_script_probe_is_not_treated_as_undiscoverable() -> None:
    """A repo-relative wrapper probe (vue_tsc) cannot prove undiscoverability."""
    installer = _installer()
    tool = ManifestTool(
        name="vue_tsc",
        version="3.1.5",
        min_version="3.1.5",
        install_type="npm",
        install_package="vue-tsc",
        version_command=("bash", "scripts/ci/resolve-vue-tsc-version.sh"),
    )

    with (
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
        # The wrapper's host binary resolves, the tool's own name does not;
        # neither fact says anything about the installed package.
        patch("lintro.tools.core.tool_installer.shutil.which", return_value=None),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = installer._run_install(tool, "bun add -g vue-tsc@3.1.5")

    assert_that(result.outcome).is_equal_to(InstallOutcome.SUCCESS)


def test_cargo_subcommand_probe_is_not_treated_as_undiscoverable() -> None:
    """``cargo audit --version`` probes cargo, not the installed subcommand."""
    installer = _installer()
    tool = ManifestTool(
        name="cargo_audit",
        version="0.21.0",
        min_version="0.21.0",
        install_type="cargo",
        install_package="cargo-audit",
        version_command=("cargo", "audit", "--version"),
    )

    with (
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
        patch("lintro.tools.core.tool_installer.shutil.which", return_value=None),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = installer._run_install(tool, "cargo install cargo-audit")

    assert_that(result.outcome).is_equal_to(InstallOutcome.SUCCESS)


def test_probe_failure_never_reaches_known_invalid() -> None:
    """A probe that errors out must not mark the install unresolved."""
    installer = _installer()
    tool = ManifestTool(
        name="vue_tsc",
        version="3.1.5",
        min_version="3.1.5",
        install_type="npm",
        install_package="vue-tsc",
        version_command=("bash", "scripts/ci/resolve-vue-tsc-version.sh"),
    )

    with (
        patch(
            "lintro.tools.core.tool_installer.subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="", stderr=""),
                OSError("boom"),
            ],
        ),
        patch("lintro.tools.core.tool_installer.shutil.which", return_value=None),
    ):
        result = installer._run_install(tool, "bun add -g vue-tsc@3.1.5")

    assert_that(result.success).is_true()
    assert_that(unresolved_tool_names([result])).is_empty()


def test_manual_hint_is_manual_blocked() -> None:
    """A prose hint is reported as manual/prerequisite-blocked."""
    installer = _installer()
    tool = ManifestTool(
        name="somebinary",
        version="1.0.0",
        min_version="1.0.0",
        install_type="binary",
    )

    with patch.object(installer, "_install_via_script", return_value=None):
        result = installer._run_install(tool, "See https://example.com/releases")

    assert_that(result.outcome).is_equal_to(InstallOutcome.MANUAL_BLOCKED)


def test_execute_continues_after_failure_and_timeout() -> None:
    """Regression: a failure and a timeout must not abort later actions."""
    installer = _installer()
    first = _tool("golangci_lint")
    second = _tool("clippy")
    third = _tool("ruff")

    plan = InstallPlan(
        to_install=[
            (first, "brew install golangci_lint"),
            (second, "rustup component add clippy"),
        ],
        to_upgrade=[(third, "0.1.0", "pip install --upgrade ruff")],
    )

    def _fake_run(
        cmd: list[str],
        **_kwargs: object,
    ) -> MagicMock:
        """Fail the first command, time out the second, pass the third.

        Args:
            cmd: Argv of the command being run.
            **_kwargs: Ignored subprocess keyword arguments.

        Returns:
            A completed-process double.

        Raises:
            subprocess.TimeoutExpired: For the simulated hanging command.
        """
        if "golangci_lint" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="No available formula")
        if "clippy" in cmd:
            raise subprocess.TimeoutExpired(cmd="rustup", timeout=300)
        if list(cmd[:2]) == ["ruff", "--version"]:
            return MagicMock(returncode=0, stdout="ruff 1.2.0", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "lintro.tools.core.tool_installer.subprocess.run",
            side_effect=_fake_run,
        ),
        patch(
            "lintro.tools.core.tool_installer.shutil.which",
            return_value="/usr/local/bin/ruff",
        ),
    ):
        results = installer.execute(plan)

    assert_that(results).is_length(3)
    assert_that([r.outcome for r in results]).is_equal_to(
        [
            InstallOutcome.FAILED,
            InstallOutcome.TIMED_OUT,
            InstallOutcome.SUCCESS,
        ],
    )
    assert_that([r.tool.name for r in results]).is_equal_to(
        ["golangci_lint", "clippy", "ruff"],
    )
    assert_that([(r.step, r.total_steps) for r in results]).is_equal_to(
        [(1, 3), (2, 3), (3, 3)],
    )
