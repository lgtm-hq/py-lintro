"""Tool installation planning and execution.

Handles installing, upgrading, and managing external tools used by lintro.
Delegates to the appropriate package manager (pip, npm, cargo, rustup, or
install-tools.sh for binary downloads) based on the tool's install type.

Usage:
    from lintro.tools.core.tool_installer import ToolInstaller
    from lintro.tools.core.tool_registry import ManifestRegistry
    from lintro.tools.core.install_context import RuntimeContext

    registry = ManifestRegistry.load()
    context = RuntimeContext.detect()
    installer = ToolInstaller(registry, context)

    plan = installer.plan(tools=["hadolint", "gitleaks"])
    results = installer.execute(plan)
"""

from __future__ import annotations

import shlex
import shutil
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False
import time
from pathlib import Path

from loguru import logger

from lintro.enums.install_outcome import InstallOutcome
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_hints import (
    SCRIPT_HINT_PREFIX,
    has_install_script,
    install_script_path,
    is_brew_managed,
    is_manual_hint,
)
from lintro.tools.core.install_plan import InstallPlan, InstallResult
from lintro.tools.core.install_strategies import get_strategy
from lintro.tools.core.install_strategies.package_names import script_tool_name
from lintro.tools.core.tool_registry import ManifestRegistry, ManifestTool
from lintro.tools.core.version_parsing import (
    compare_versions,
    extract_version_from_output,
)

#: Version-probe entrypoints that run a wrapper or host binary rather than the
#: tool's own executable, so they cannot prove the tool is on PATH.
_INDIRECT_PROBE_COMMANDS = frozenset({"sh", "bash", "cargo"})

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
        registry: ManifestRegistry,
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
        return is_manual_hint(hint)

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
            meets_min = self._version_meets_minimum(
                installed_version,
                tool.min_version,
            )
            meets_recommended = self._version_meets_minimum(
                installed_version,
                tool.version,
            )
            if meets_recommended:
                plan.already_ok.append(tool)
            elif upgrade or (not meets_min and tool.min_version != tool.version):
                skip_reason = self._check_prerequisites(tool)
                if skip_reason:
                    plan.manual.append((tool, skip_reason))
                    return
                hint = self._get_install_command(tool, upgrade=True)
                if self._is_manual_hint(hint):
                    if self._has_install_script(tool):
                        hint = f"{SCRIPT_HINT_PREFIX} ({tool.name})"
                    else:
                        plan.manual.append((tool, hint))
                        return
                plan.to_upgrade.append((tool, installed_version, hint))
            else:
                plan.outdated.append((tool, installed_version))
            return

        # Missing prerequisites → manual install (not a hard skip)
        skip_reason = self._check_prerequisites(tool)
        if skip_reason:
            plan.manual.append((tool, skip_reason))
            return

        hint = self._get_install_command(tool)
        if self._is_manual_hint(hint):
            if self._has_install_script(tool):
                hint = f"{SCRIPT_HINT_PREFIX} ({tool.name})"
            else:
                plan.manual.append((tool, hint))
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

        command = self._resolved_version_command(tool)
        main_cmd = command[0]
        if (
            main_cmd not in ("sh", "bash", "cargo")
            and not Path(main_cmd).is_absolute()
            and not shutil.which(main_cmd)
        ):
            return None

        try:
            result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
                command,
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
        except ValueError as exc:
            # Unknown/unparseable tool output must not abort an install batch.
            logger.debug(f"Version probe failed for {tool.name}: {exc}")
            return None

    def _resolved_version_command(self, tool: ManifestTool) -> list[str]:
        """Resolve a tool's version command the way the run will resolve it.

        For npm-installed tools the manifest's ``version_command`` names a bare
        binary, which only ever finds a global install. Since #1811 a run
        resolves ``node_modules/.bin`` first, so planning against ``PATH`` would
        report a tool as missing (or as the wrong version) while checks happily
        use the project-local one — the exact "two authorities" split #2005 is
        about. Route npm tools through the same command-builder registry the
        executor uses, anchored on the detected project root.

        Args:
            tool: Tool whose version command is being resolved.

        Returns:
            Argv list to run for the version probe.
        """
        command = list(tool.version_command)
        if tool.install_type != "npm":
            return command

        from lintro.plugins.execution_preparation import get_executable_command

        project = self._context.environment.node_project
        resolved = get_executable_command(
            tool.name,
            cwd=project.root if project is not None else None,
        )
        return [*resolved, *command[1:]]

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

    def _install_cwd(self, tool: ManifestTool) -> Path | None:
        """Return the directory an install command should run from.

        Args:
            tool: Tool being installed.

        Returns:
            The detected Node project root for a project-local npm install, or
            None to use the process working directory.
        """
        env = self._context.environment
        if tool.install_type != "npm" or env.installs_globally():
            return None
        return env.node_project.root if env.node_project is not None else None

    @staticmethod
    def _is_brew_managed(package: str) -> bool:
        """Check if a package is installed via Homebrew.

        Args:
            package: Homebrew formula name.

        Returns:
            True if brew manages this package.
        """
        return is_brew_managed(package)

    def execute(self, plan: InstallPlan) -> list[InstallResult]:
        """Execute an installation plan.

        Every planned action is attempted, in order. A non-zero exit or a
        timeout for one tool never aborts the batch: the failure is recorded
        and the next action still runs, so the returned list always has one
        entry per planned action.

        Args:
            plan: The plan to execute.

        Returns:
            List of results for each install/upgrade action, in plan order.
        """
        results: list[InstallResult] = []
        actions: list[tuple[ManifestTool, str]] = [
            *plan.to_install,
            *[(tool, command) for tool, _current_ver, command in plan.to_upgrade],
        ]
        total = len(actions)

        for index, (tool, command) in enumerate(actions, start=1):
            logger.info(f"[{index}/{total}] {tool.name}: {command}")
            result = self._run_install(tool, command)
            result.step = index
            result.total_steps = total
            logger.info(
                f"[{index}/{total}] {tool.name}: "
                f"{result.outcome.label} — {result.message}",
            )
            results.append(result)

        return results

    @staticmethod
    def _verify_discoverable(tool: ManifestTool) -> bool:
        """Check whether a tool is discoverable after a successful install.

        Only the executable lookup is used as evidence. A version probe can
        fail for reasons that have nothing to do with discoverability — a
        repo-relative wrapper script (``bash scripts/...``), a subcommand of
        another binary (``cargo audit``), unparseable output, or a transient
        error — and reporting those as NOT_DISCOVERABLE would mark a genuinely
        successful install as failed and suppress the tool from later quick
        fixes.

        Args:
            tool: Tool that was just installed.

        Returns:
            False only when the tool's own executable is provably absent from
            PATH; True when it resolves or when no on-PATH probe applies.
        """
        if not tool.version_command:
            # Nothing to probe with — trust the exit code.
            return True

        main_cmd = tool.version_command[0]
        if main_cmd in _INDIRECT_PROBE_COMMANDS or "/" in main_cmd:
            # The probe runs a wrapper/host binary, not the tool itself, so it
            # cannot answer whether the tool ended up discoverable.
            return True

        return shutil.which(main_cmd) is not None

    _INSTALL_TIMEOUT_SECONDS = 300

    def _run_install(
        self,
        tool: ManifestTool,
        command: str,
    ) -> InstallResult:
        """Run an install command for a tool and classify the outcome.

        Args:
            tool: Tool being installed.
            command: Shell command string.

        Returns:
            InstallResult carrying a classified :class:`InstallOutcome`; this
            method never raises, so the caller can continue with the next tool.
        """
        start = time.monotonic()

        try:
            # Script-backed installs: the planner sets the script hint prefix
            # when a helper script is available for binary tools
            if command.startswith(SCRIPT_HINT_PREFIX):
                result = self._install_via_script(tool)
                if result:
                    return result
                return InstallResult(
                    tool=tool,
                    outcome=InstallOutcome.MANUAL_BLOCKED,
                    message="install-tools.sh not found",
                    duration_seconds=time.monotonic() - start,
                    command=command,
                )

            # Non-executable hints: try install script for binary tools,
            # otherwise report as manual
            if is_manual_hint(command):
                if tool.install_type == "binary":
                    result = self._install_via_script(tool)
                    if result:
                        return result
                return InstallResult(
                    tool=tool,
                    outcome=InstallOutcome.MANUAL_BLOCKED,
                    message=f"Manual install required: {command}",
                    duration_seconds=0.0,
                    command=command,
                )

            # Otherwise run the command directly. Node package managers walk up
            # to the nearest package.json, but running from the project root
            # lintro actually detected removes the ambiguity — a nested cwd must
            # not add a dependency to a manifest lintro never looked at (#2005).
            proc = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=self._INSTALL_TIMEOUT_SECONDS,
                check=False,
                cwd=self._install_cwd(tool),
            )
            duration = time.monotonic() - start

            if proc.returncode == 0:
                return self._verified_result(
                    tool=tool,
                    command=command,
                    message="Installed successfully",
                    duration=duration,
                )
            return InstallResult(
                tool=tool,
                outcome=InstallOutcome.FAILED,
                message=f"Command failed (exit {proc.returncode}): {proc.stderr[:200]}",
                duration_seconds=duration,
                command=command,
            )
        except subprocess.TimeoutExpired:
            minutes = self._INSTALL_TIMEOUT_SECONDS // 60
            return InstallResult(
                tool=tool,
                outcome=InstallOutcome.TIMED_OUT,
                message=f"Installation timed out ({minutes} min)",
                duration_seconds=time.monotonic() - start,
                command=command,
            )
        except OSError as e:
            return InstallResult(
                tool=tool,
                outcome=InstallOutcome.FAILED,
                message=f"OS error: {e}",
                duration_seconds=time.monotonic() - start,
                command=command,
            )

    def _verified_result(
        self,
        *,
        tool: ManifestTool,
        command: str,
        message: str,
        duration: float,
    ) -> InstallResult:
        """Build the result for a command that exited zero.

        Args:
            tool: Tool that was installed.
            command: Command that was run.
            message: Success message to use when the tool is discoverable.
            duration: Elapsed seconds for the install.

        Returns:
            InstallResult with SUCCESS or NOT_DISCOVERABLE.
        """
        if self._verify_discoverable(tool):
            return InstallResult(
                tool=tool,
                outcome=InstallOutcome.SUCCESS,
                message=message,
                duration_seconds=duration,
                command=command,
            )
        return InstallResult(
            tool=tool,
            outcome=InstallOutcome.NOT_DISCOVERABLE,
            message=(
                "Install command succeeded but "
                f"{tool.name} is still not discoverable on PATH"
            ),
            duration_seconds=duration,
            command=command,
        )

    @staticmethod
    def _has_install_script(tool: ManifestTool) -> bool:
        """Check if an install script exists for a binary tool.

        Args:
            tool: Tool to check.

        Returns:
            True if a script can handle this tool.
        """
        return has_install_script(tool)

    def _install_via_script(self, tool: ManifestTool) -> InstallResult | None:
        """Try to install a binary tool via install-tools.sh.

        Args:
            tool: Binary tool to install.

        Returns:
            InstallResult if script was found and executed, None otherwise.
        """
        script = install_script_path()
        if not script:
            logger.debug(
                "install-tools.sh not found for binary install "
                "(only available in dev/Homebrew installs, not pip)",
            )
            return None

        tool_arg = script_tool_name(tool.name)
        cmd = ["bash", str(script), "--tools", tool_arg]
        command = shlex.join(cmd)

        start = time.monotonic()
        try:
            proc = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._INSTALL_TIMEOUT_SECONDS,
            )
            duration = time.monotonic() - start

            if proc.returncode == 0:
                return self._verified_result(
                    tool=tool,
                    command=command,
                    message="Installed via install-tools.sh",
                    duration=duration,
                )
            return InstallResult(
                tool=tool,
                outcome=InstallOutcome.FAILED,
                message=f"install-tools.sh failed: {proc.stderr[:200]}",
                duration_seconds=duration,
                command=command,
            )
        except subprocess.TimeoutExpired:
            minutes = self._INSTALL_TIMEOUT_SECONDS // 60
            return InstallResult(
                tool=tool,
                outcome=InstallOutcome.TIMED_OUT,
                message=f"install-tools.sh timed out ({minutes} min)",
                duration_seconds=time.monotonic() - start,
                command=command,
            )
        except OSError as exc:
            return InstallResult(
                tool=tool,
                outcome=InstallOutcome.FAILED,
                message=f"install-tools.sh execution failed: {exc}",
                duration_seconds=time.monotonic() - start,
                command=command,
            )
