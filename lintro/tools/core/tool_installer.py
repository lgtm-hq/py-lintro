"""Tool installation planning and execution.

Handles installing, upgrading, and managing external tools used by lintro.
Delegates to the appropriate package manager (pip, npm, cargo, rustup, or
install-tools.sh for binary downloads) based on the tool's install type.

Usage:
    from lintro.tools.core.tool_installer import ToolInstaller
    from lintro.tools.core.tool_registry import ToolRegistry
    from lintro.tools.core.install_context import RuntimeContext

    registry = ToolRegistry.load()
    context = RuntimeContext.detect()
    installer = ToolInstaller(registry, context)

    plan = installer.plan(tools=["hadolint", "gitleaks"])
    results = installer.execute(plan)
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from pathlib import Path

from loguru import logger

from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_plan import InstallPlan, InstallResult
from lintro.tools.core.install_strategies import get_strategy
from lintro.tools.core.tool_registry import ManifestTool, ToolRegistry
from lintro.tools.core.version_parsing import (
    compare_versions,
    extract_version_from_output,
)

# Re-export so existing ``from lintro.tools.core.tool_installer import InstallPlan``
# continues to work.
__all__ = [
    "InstallPlan",
    "InstallResult",
    "ToolInstaller",
]


class ToolInstaller:
    """Plans and executes tool installations.

    Uses the RuntimeContext to generate appropriate install commands for the
    current platform and installation method.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        context: RuntimeContext,
    ) -> None:
        """Initialize the installer with registry and context."""
        self._registry = registry
        self._context = context

    def plan(
        self,
        tools: list[str] | None = None,
        *,
        profile: str | None = None,
        upgrade: bool = False,
        detected_langs: list[str] | None = None,
    ) -> InstallPlan:
        """Create an installation plan.

        Args:
            tools: Specific tool names to install. If None, uses profile.
            profile: Profile name to resolve tools from.
            upgrade: If True, upgrade already-installed tools.
            detected_langs: Detected languages for profile resolution.

        Returns:
            InstallPlan describing what will happen.
        """
        plan = InstallPlan()

        # Determine which tools to consider
        if tools is not None:
            tools = list(dict.fromkeys(tools))  # deduplicate, preserve order
            unknown = [n for n in tools if n not in self._registry]
            if unknown:
                logger.warning(
                    "Unknown tools (not in registry): {}",
                    ", ".join(unknown),
                )
            tool_list = [self._registry.get(n) for n in tools if n in self._registry]
        elif profile:
            tool_list = self._registry.tools_for_profile(
                profile,
                detected_langs,
            )
        else:
            tool_list = self._registry.all_tools()

        for tool in tool_list:
            self._plan_tool(plan, tool, upgrade=upgrade)

        return plan

    @staticmethod
    def _is_manual_hint(hint: str) -> bool:
        """Check if an install hint is a human-only message, not an executable command.

        Args:
            hint: Install/upgrade command string.

        Returns:
            True if the hint requires manual action.
        """
        return (
            hint.startswith(("See ", "Install ", "Upgrade "))
            or "https://" in hint
            or "http://" in hint
        )

    def _plan_tool(
        self,
        plan: InstallPlan,
        tool: ManifestTool,
        *,
        upgrade: bool,
    ) -> None:
        """Plan installation for a single tool.

        Args:
            plan: Plan to add to.
            tool: Tool to plan for.
            upgrade: Whether to upgrade if already installed.
        """
        # Check current installation status first — tool may already be on PATH
        # even if its package manager isn't available
        installed_version = self._get_installed_version(tool)

        if installed_version:
            is_current = self._version_meets_minimum(
                installed_version,
                tool.version,
            )
            if is_current:
                plan.already_ok.append(tool)
            elif upgrade:
                skip_reason = self._check_prerequisites(tool)
                if skip_reason:
                    plan.skipped.append((tool, skip_reason))
                    return
                hint = self._get_install_command(tool, upgrade=True)
                if self._is_manual_hint(hint):
                    if self._has_install_script(tool):
                        hint = f"via install-tools.sh ({tool.name})"
                    else:
                        plan.skipped.append(
                            (tool, f"manual upgrade required: {hint}"),
                        )
                        return
                plan.to_upgrade.append((tool, installed_version, hint))
            else:
                plan.outdated.append((tool, installed_version))
            return

        # Only check prerequisites when we need to install
        skip_reason = self._check_prerequisites(tool)
        if skip_reason:
            plan.skipped.append((tool, skip_reason))
            return

        hint = self._get_install_command(tool)
        if self._is_manual_hint(hint):
            if self._has_install_script(tool):
                hint = f"via install-tools.sh ({tool.name})"
            else:
                plan.skipped.append((tool, f"manual install required: {hint}"))
                return
        plan.to_install.append((tool, hint))

    def _check_prerequisites(self, tool: ManifestTool) -> str | None:
        """Check if prerequisites for installing a tool are met.

        Delegates to the install strategy for the tool's install_type.

        Args:
            tool: Tool to check.

        Returns:
            Skip reason string, or None if prerequisites are met.
        """
        strategy = get_strategy(tool.install_type)
        if strategy is None:
            return None
        return strategy.check_prerequisites(self._context.environment, tool.name)

    def _get_installed_version(self, tool: ManifestTool) -> str | None:
        """Get the currently installed version of a tool.

        Args:
            tool: Tool to check.

        Returns:
            Version string or None if not installed.
        """
        if not tool.version_command:
            return None

        main_cmd = tool.version_command[0]
        if main_cmd not in ("sh", "bash", "cargo") and not shutil.which(main_cmd):
            return None

        try:
            result = subprocess.run(
                tool.version_command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return None
            output = result.stdout + result.stderr
            return extract_version_from_output(output, tool.name)
        except (subprocess.TimeoutExpired, OSError):
            return None

    @staticmethod
    def _version_meets_minimum(installed: str, minimum: str) -> bool:
        """Check if installed version meets the minimum requirement.

        Delegates to version_parsing.compare_versions which uses the
        packaging library for robust PEP 440 version comparison.

        Args:
            installed: Installed version string.
            minimum: Minimum required version string.

        Returns:
            True if installed >= minimum.
        """
        try:
            return compare_versions(installed, minimum) >= 0
        except ValueError as exc:
            logger.debug(
                f"Version comparison failed for {installed!r} vs {minimum!r}: {exc}",
            )
            return False

    def _get_install_command(
        self,
        tool: ManifestTool,
        *,
        upgrade: bool = False,
    ) -> str:
        """Get the install command string for a tool.

        Delegates to the install strategy for the tool's install_type.

        Args:
            tool: Tool to generate command for.
            upgrade: If True, generate an upgrade command.

        Returns:
            Shell command string.
        """
        strategy = get_strategy(tool.install_type)
        env = self._context.environment
        _args = (
            env,
            tool.name,
            tool.version,
            tool.install_package,
            tool.install_component,
        )
        if strategy is None:
            return (
                f"Upgrade {tool.name} manually"
                if upgrade
                else f"Install {tool.name} manually"
            )
        if upgrade:
            hint = strategy.upgrade_hint(*_args)
            # For brew upgrades, validate that brew actually manages this
            # package — if not, use the non-brew install command instead
            # (strategies may prefer brew when available, so we can't just
            # call install_hint which might also suggest brew).
            if hint.startswith("brew upgrade"):
                brew_pkg = hint.split()[-1] if hint.split() else tool.name
                if not self._is_brew_managed(brew_pkg):
                    pkg = tool.install_package or tool.name
                    hint = f"Upgrade {pkg} manually (not managed by Homebrew)"
            return hint
        return strategy.install_hint(*_args)

    @staticmethod
    def _is_brew_managed(package: str) -> bool:
        """Check if a package is installed via Homebrew.

        Args:
            package: Homebrew formula name.

        Returns:
            True if brew manages this package.
        """
        if not shutil.which("brew"):
            return False
        try:
            result = subprocess.run(
                ["brew", "list", "--formula", package],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def execute(self, plan: InstallPlan) -> list[InstallResult]:
        """Execute an installation plan.

        Args:
            plan: The plan to execute.

        Returns:
            List of results for each install/upgrade action.
        """
        results: list[InstallResult] = []

        for tool, command in plan.to_install:
            result = self._run_install(tool, command)
            results.append(result)

        for tool, _current_ver, command in plan.to_upgrade:
            result = self._run_install(tool, command)
            results.append(result)

        return results

    def _run_install(
        self,
        tool: ManifestTool,
        command: str,
    ) -> InstallResult:
        """Run an install command for a tool.

        Args:
            tool: Tool being installed.
            command: Shell command string.

        Returns:
            InstallResult.
        """
        logger.info(f"Installing {tool.name}: {command}")
        start = time.monotonic()

        try:
            # Script-backed installs: the planner sets "via install-tools.sh"
            # when a helper script is available for binary tools
            if command.startswith("via install-tools.sh"):
                result = self._install_via_script(tool)
                if result:
                    return result
                return InstallResult(
                    tool=tool,
                    success=False,
                    message="install-tools.sh not found",
                    duration_seconds=time.monotonic() - start,
                )

            # Non-executable hints: try install script for binary tools,
            # otherwise report as manual
            if self._is_manual_hint(command):
                if tool.install_type == "binary":
                    result = self._install_via_script(tool)
                    if result:
                        return result
                return InstallResult(
                    tool=tool,
                    success=False,
                    message=f"Manual install required: {command}",
                    duration_seconds=0.0,
                )

            # Otherwise run the command directly
            proc = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            duration = time.monotonic() - start

            if proc.returncode == 0:
                return InstallResult(
                    tool=tool,
                    success=True,
                    message="Installed successfully",
                    duration_seconds=duration,
                )
            return InstallResult(
                tool=tool,
                success=False,
                message=f"Command failed (exit {proc.returncode}): {proc.stderr[:200]}",
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired:
            return InstallResult(
                tool=tool,
                success=False,
                message="Installation timed out (5 min)",
                duration_seconds=time.monotonic() - start,
            )
        except OSError as e:
            return InstallResult(
                tool=tool,
                success=False,
                message=f"OS error: {e}",
                duration_seconds=time.monotonic() - start,
            )

    @staticmethod
    def _has_install_script(tool: ManifestTool) -> bool:
        """Check if an install script exists for a binary tool.

        Reuses the same script lookup as _install_via_script.

        Args:
            tool: Tool to check.

        Returns:
            True if a script can handle this tool.
        """
        if tool.install_type != "binary":
            return False
        script = (
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "utils"
            / "install-tools.sh"
        )
        return script.exists()

    def _install_via_script(self, tool: ManifestTool) -> InstallResult | None:
        """Try to install a binary tool via install-tools.sh.

        Args:
            tool: Binary tool to install.

        Returns:
            InstallResult if script was found and executed, None otherwise.
        """
        # Look for install-tools.sh relative to the lintro package
        script_paths = [
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "utils"
            / "install-tools.sh",
        ]

        script = None
        for p in script_paths:
            if p.exists():
                script = p
                break

        if not script:
            logger.debug(
                "install-tools.sh not found for binary install "
                "(only available in dev/Homebrew installs, not pip)",
            )
            return None

        tool_arg = tool.name.replace("_", "-")
        cmd = ["bash", str(script), "--tools", tool_arg]

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
            duration = time.monotonic() - start

            if proc.returncode == 0:
                return InstallResult(
                    tool=tool,
                    success=True,
                    message="Installed via install-tools.sh",
                    duration_seconds=duration,
                )
            return InstallResult(
                tool=tool,
                success=False,
                message=f"install-tools.sh failed: {proc.stderr[:200]}",
                duration_seconds=duration,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return InstallResult(
                tool=tool,
                success=False,
                message=f"install-tools.sh execution failed: {exc}",
                duration_seconds=time.monotonic() - start,
            )
