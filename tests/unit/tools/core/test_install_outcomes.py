"""Tests for install outcome classification and batch continuation."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only TimeoutExpired is referenced; no process is spawned
import sys
import sysconfig
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.cli_utils.install_output import unresolved_tool_names
from lintro.enums.install_context import InstallContext, PackageManager
from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_plan import InstallPlan
from lintro.tools.core.install_quickfix import build_quick_fix
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.node_project import detect_node_project
from lintro.tools.core.tool_installer import (
    ToolInstaller,
    is_resolved_command_discoverable,
    resolve_version_command,
)
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
    assert_that(result.message).contains(str(Path(sysconfig.get_path("scripts"))))
    assert_that(result.message).contains("PATH")
    assert_that(result.message).does_not_contain("needs manual action")
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
            MagicMock(returncode=0, stdout="no version here", stderr=""),
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


def _write_node_project(root: Path) -> Path:
    """Create a minimal npm-locked Node project.

    Args:
        root: Directory to write the project into.

    Returns:
        The project root.
    """
    (root / "package.json").write_text(
        json.dumps({"name": "demo"}),
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text("", encoding="utf-8")
    return root


def _node_installer(root: Path) -> ToolInstaller:
    """Build an installer anchored on a Node project.

    Args:
        root: Project root with a package.json.

    Returns:
        ToolInstaller instance.
    """
    return ToolInstaller(
        ManifestRegistry.load(),
        RuntimeContext(
            install_context=InstallContext.PIP,
            platform_label="Linux x86_64",
            environment=InstallEnvironment(
                install_context=InstallContext.PIP,
                available_managers=frozenset({PackageManager.NPM, PackageManager.UV}),
                node_project=detect_node_project(root),
            ),
            is_ci=False,
        ),
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="fake shell-script binary is not executable on Windows",
)
def test_project_local_npm_binary_is_discoverable(tmp_path: Path) -> None:
    """A binary only under node_modules/.bin is discoverable, not PATH.

    After a project-local ``npm install -D`` the executable is not on PATH.
    Planning, post-install verification, and doctor must still treat it as
    installed.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_node_project(tmp_path)
    registry = ManifestRegistry.load()
    tool = registry.get("prettier")
    local_bin = tmp_path / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    binary = local_bin / "prettier"
    binary.write_text(f'#!/bin/sh\necho "{tool.version}"\n', encoding="utf-8")
    binary.chmod(0o755)

    installer = _node_installer(tmp_path)

    with (
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
        patch("lintro.tools.core.tool_installer.shutil.which", return_value=None),
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=tool.version, stderr=""),
        ]
        assert_that(installer._verify_discoverable(tool)).is_true()
        result = installer._run_install(tool, "npm install -D prettier")

    assert_that(result.outcome).is_not_equal_to(InstallOutcome.NOT_DISCOVERABLE)
    assert_that(result.outcome).is_equal_to(InstallOutcome.SUCCESS)


def test_bunx_npx_fallback_is_not_treated_as_discoverable() -> None:
    """The registry fallback is a fetch, not an install."""
    assert_that(
        is_resolved_command_discoverable(["bunx", "prettier@3.9.4"]),
    ).is_false()
    assert_that(
        is_resolved_command_discoverable(["npx", "prettier@3.9.4"]),
    ).is_false()

    installer = _installer()
    tool = ManifestTool(
        name="prettier",
        version="3.9.4",
        min_version="3.9.4",
        install_type="npm",
        install_package="prettier",
        version_command=("prettier", "--version"),
    )
    with (
        patch(
            "lintro.plugins.execution_preparation.get_executable_command",
            return_value=["bunx", "prettier@3.9.4"],
        ),
        patch("lintro.tools.core.tool_installer.shutil.which", return_value=None),
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert_that(installer._verify_discoverable(tool)).is_false()
        result = installer._run_install(tool, "npm install -D prettier@3.9.4")

    assert_that(result.outcome).is_equal_to(InstallOutcome.NOT_DISCOVERABLE)


def test_upgrade_exit_0_still_outdated_is_not_re_suggested() -> None:
    """Exit 0 with a version still below min_version is a non-success.

    The tool is added to known_invalid so post-fix quick-fix does not
    re-emit the identical command within this process.
    """
    installer = _installer()
    tool = ManifestTool(
        name="ruff",
        version="2.0.0",
        min_version="2.0.0",
        install_type="pip",
        install_package="ruff",
        version_command=("ruff", "--version"),
    )

    with (
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
        patch(
            "lintro.tools.core.tool_installer.shutil.which",
            return_value="/usr/local/bin/ruff",
        ),
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="ruff 1.0.0", stderr=""),
        ]
        result = installer._run_install(tool, "pip install --upgrade ruff")

    assert_that(result.outcome).is_equal_to(InstallOutcome.STILL_OUTDATED)
    assert_that(result.success).is_false()
    assert_that(result.outcome.is_retryable).is_false()
    assert_that(result.message).contains("1.0.0")
    assert_that(result.message).contains("2.0.0")

    unresolved = unresolved_tool_names([result])
    assert_that(unresolved).is_equal_to(["ruff"])

    quick_fix = build_quick_fix(
        [(tool, True)],
        InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset({PackageManager.UV}),
        ),
        known_invalid=unresolved,
    )
    assert_that(quick_fix.commands).is_empty()
    assert_that([name for name, _reason in quick_fix.blocked]).is_equal_to(["ruff"])


def test_not_discoverable_message_names_the_destination_directory(
    tmp_path: Path,
) -> None:
    """A PATH outcome for a project-local npm add names node_modules/.bin.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    _write_node_project(tmp_path)
    installer = _node_installer(tmp_path)
    tool = ManifestTool(
        name="prettier",
        version="3.9.4",
        min_version="3.9.4",
        install_type="npm",
        install_package="prettier",
        version_command=("prettier", "--version"),
    )
    expected_bin = tmp_path.resolve() / "node_modules" / ".bin"

    with (
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
        patch("lintro.tools.core.tool_installer.shutil.which", return_value=None),
        patch(
            "lintro.plugins.execution_preparation.get_executable_command",
            return_value=["prettier"],
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = installer._run_install(tool, "npm install -D prettier@3.9.4")

    assert_that(result.outcome).is_equal_to(InstallOutcome.NOT_DISCOVERABLE)
    assert_that(result.message).contains(str(expected_bin))
    assert_that(result.message).contains("expected under")
    assert_that(result.message).contains("PATH")
    assert_that(result.message).does_not_contain("was installed to")
    assert_that(result.message).does_not_contain("needs manual action")


@pytest.mark.parametrize(
    ("argv0", "rest"),
    [
        ("bash", ("scripts/vue-tsc-version.sh",)),
        ("sh", ("scripts/vue-tsc-version.sh",)),
        ("cargo", ("audit", "--version")),
        ("scripts/probe.sh", ()),
    ],
    ids=["bash", "sh", "cargo", "relative-path"],
)
def test_npm_wrapper_probes_are_not_rewritten_by_node_resolution(
    tmp_path: Path,
    argv0: str,
    rest: tuple[str, ...],
) -> None:
    """Wrapper probes stay as the manifest wrote them, even for npm tools.

    Args:
        tmp_path: Temporary directory provided by pytest.
        argv0: First element of the manifest version command.
        rest: Remaining argv.
    """
    _write_node_project(tmp_path)
    installer = _node_installer(tmp_path)
    command = (argv0, *rest)
    tool = ManifestTool(
        name="vue_tsc",
        version="3.0.0",
        min_version="3.0.0",
        install_type="npm",
        install_package="vue-tsc",
        version_command=command,
    )
    with patch(
        "lintro.plugins.execution_preparation.get_executable_command",
    ) as mock_resolve:
        resolved = resolve_version_command(tool, context=installer._context)

    assert_that(resolved).is_equal_to(list(command))
    mock_resolve.assert_not_called()
    assert_that(is_resolved_command_discoverable(resolved)).is_true()


def test_global_npm_destination_uses_which_parent_not_symlink_target(
    tmp_path: Path,
) -> None:
    """Global npm PATH names the executable's parent, not the symlink target.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    bindir = tmp_path / "node" / "v22.22.2" / "bin"
    bindir.mkdir(parents=True)
    real_npm = tmp_path / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    real_npm.parent.mkdir(parents=True)
    real_npm.write_text("")
    link = bindir / "npm"
    link.symlink_to(real_npm)

    installer = _installer()
    tool = ManifestTool(
        name="prettier",
        version="3.9.4",
        min_version="3.9.4",
        install_type="npm",
        install_package="prettier",
        version_command=("prettier", "--version"),
    )
    with (
        patch.object(installer, "_install_cwd", return_value=None),
        patch(
            "lintro.tools.core.tool_installer.shutil.which",
            return_value=str(link),
        ),
    ):
        dest = installer._install_destination_dir(tool)

    assert_that(dest).is_equal_to(bindir)
    assert_that(str(dest)).does_not_contain("node_modules")
