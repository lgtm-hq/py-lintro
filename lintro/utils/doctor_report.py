"""Print-free data layer behind ``lintro doctor``.

``lintro doctor`` grew as one 1000-line click command that interleaved probing
with rendering: the only way to learn whether a binary was present was to let
the command print a Rich table and read it back. That is fine for a terminal
and useless for anything else, which is why the MCP ``lintro_doctor`` tool
(issue #1240) needed the probes extracted rather than reimplemented.

Two layers live here:

* The **raw probes** — :class:`ToolCheckResult`, :func:`check_tool`,
  :func:`collect_tool_checks`, :func:`mcp_extra_status` — moved verbatim out of
  :mod:`lintro.cli_utils.commands.doctor`, which now imports them. The CLI's
  output is unchanged; what changed is that its data collection is importable,
  testable, and free of ``click``/``rich``.
* The **health report** — :class:`DoctorReport`, a list of
  :class:`DoctorCheck` records shaped ``{check, status, detail, remediation}``.
  This is the agent-facing projection: one record per *condition an operator
  can act on* rather than one per tool, because per-tool installation detail is
  already the job of ``lintro_list_tools``.

Nothing here prints, and nothing here raises for an unhealthy environment: a
broken config, a missing provider, and an absent binary are all *data*. The
CLI keeps owning exit codes.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess is the only way to ask a binary its version; every call below is a fixed argv list with shell=False
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, Final

from lintro.enums.tool_status import ToolStatus
from lintro.tools.core.install_strategies import get_strategy
from lintro.tools.core.tool_registry import ManifestRegistry, ManifestTool
from lintro.tools.core.update_channels import VersionAdvisory
from lintro.tools.core.version_checking import build_version_advisory
from lintro.tools.core.version_parsing import (
    compare_versions,
    extract_version_from_output,
)

if TYPE_CHECKING:
    from lintro.config.lintro_config import LintroConfig
    from lintro.tools.core.install_context import RuntimeContext

__all__ = [
    "DoctorCheck",
    "DoctorCheckCategory",
    "DoctorCheckStatus",
    "DoctorHealth",
    "DoctorReport",
    "ToolCheckResult",
    "check_tool",
    "collect_doctor_report",
    "collect_tool_checks",
    "mcp_extra_status",
    "tool_status_for_versions",
]

#: Wall-clock budget for a single ``<tool> --version`` probe.
VERSION_PROBE_TIMEOUT_SECONDS: Final[int] = 10


@dataclass
class ToolCheckResult:
    """Result of a tool health check.

    Attributes:
        tool: The manifest tool entry.
        status: ToolStatus value (OK, MISSING, OUTDATED, UNKNOWN).
        installed_version: Detected version string, or None.
        error: Error type if check failed.
        details: Additional error details.
        path: Filesystem path where the tool was found.
        install_hint: Context-aware install command.
        upgrade_hint: Context-aware upgrade command for outdated tools.
        advisory: Structured update advisory when outdated/incompatible.
    """

    tool: ManifestTool
    status: ToolStatus
    installed_version: str | None = None
    error: str | None = None
    details: str | None = None
    path: str | None = None
    install_hint: str = ""
    upgrade_hint: str = ""
    advisory: VersionAdvisory | None = None

    @property
    def installed(self) -> bool:
        """Return whether the tool's binary was found and answered a probe.

        ``UNKNOWN`` counts as installed: the binary ran, lintro just could not
        parse a version out of what it printed. Everything ``MISSING`` covers
        — absent from ``PATH``, non-zero exit, timeout — is reported as not
        installed, because from a caller's point of view the tool cannot run.

        Returns:
            bool: True when the tool is usable.
        """
        return self.status in (
            ToolStatus.OK,
            ToolStatus.OUTDATED,
            ToolStatus.INCOMPATIBLE,
            ToolStatus.UNKNOWN,
        )


def tool_status_for_versions(
    *,
    installed: str,
    recommended: str,
    minimum: str,
) -> ToolStatus:
    """Compare an installed version against recommended and minimum versions.

    Args:
        installed: Installed version string.
        recommended: Recommended/tested version from manifest.
        minimum: Hard minimum compatible version from manifest.

    Returns:
        ToolStatus.OK, OUTDATED, INCOMPATIBLE, or UNKNOWN.
    """
    try:
        if compare_versions(installed, minimum) < 0:
            return ToolStatus.INCOMPATIBLE
        if compare_versions(installed, recommended) < 0:
            return ToolStatus.OUTDATED
        return ToolStatus.OK
    except ValueError:
        return ToolStatus.UNKNOWN


def check_tool(*, tool: ManifestTool, context: RuntimeContext) -> ToolCheckResult:
    """Check a single tool's installation status and version.

    Args:
        tool: Manifest tool entry.
        context: Runtime context for install hints.

    Returns:
        ToolCheckResult with status and details.
    """
    strategy = get_strategy(tool.install_type)
    env = context.environment
    if strategy:
        _args = (
            env,
            tool.name,
            tool.version,
            tool.install_package,
            tool.install_component,
        )
        hint = strategy.install_hint(*_args)
        upgrade_hint = strategy.upgrade_hint(*_args)
    else:
        hint = f"Install {tool.name} manually"
        upgrade_hint = f"Upgrade {tool.name} manually"

    if not tool.version_command:
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.MISSING,
            error="no_command",
            details="No version command defined",
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )

    # Find the main executable (may be a wrapper like "sh", "cargo", etc.)
    main_cmd = tool.version_command[0]
    tool_path = shutil.which(main_cmd)

    if not tool_path:
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.MISSING,
            error="not_in_path",
            details=main_cmd,
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )

    try:
        result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
            tool.version_command,
            capture_output=True,
            text=True,
            timeout=VERSION_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
        output = result.stdout + result.stderr

        if result.returncode != 0:
            return ToolCheckResult(
                tool=tool,
                status=ToolStatus.MISSING,
                error="command_failed",
                details=f"Exit {result.returncode}: {output[:100]}",
                path=tool_path,
                install_hint=hint,
                upgrade_hint=upgrade_hint,
            )

        version = extract_version_from_output(output, tool.name)
        if not version:
            return ToolCheckResult(
                tool=tool,
                status=ToolStatus.UNKNOWN,
                error="no_version",
                details=f"Output: {output[:100]}",
                path=tool_path,
                install_hint=hint,
                upgrade_hint=upgrade_hint,
            )

        status = tool_status_for_versions(
            installed=version,
            recommended=tool.version,
            minimum=tool.min_version,
        )
        advisory = None
        final_upgrade = upgrade_hint
        if status in (ToolStatus.OUTDATED, ToolStatus.INCOMPATIBLE):
            advisory = build_version_advisory(
                tool=tool.name,
                installed=version,
                latest_known=tool.version,
                binary_path=tool_path,
                install_package=tool.install_package,
                install_type=tool.install_type,
            )
            if advisory and advisory.update_command:
                final_upgrade = advisory.update_command
        return ToolCheckResult(
            tool=tool,
            status=status,
            installed_version=version,
            path=tool_path,
            install_hint=hint,
            upgrade_hint=final_upgrade,
            advisory=advisory,
        )
    except subprocess.TimeoutExpired:
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.MISSING,
            error="timeout",
            path=tool_path,
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )
    except OSError as e:
        # FileNotFoundError is an OSError subclass; naming both said nothing.
        return ToolCheckResult(
            tool=tool,
            status=ToolStatus.MISSING,
            error="os_error",
            details=str(e),
            path=tool_path,
            install_hint=hint,
            upgrade_hint=upgrade_hint,
        )


def collect_tool_checks(
    *,
    registry: ManifestRegistry,
    context: RuntimeContext,
    config: LintroConfig | None = None,
    tool_names: list[str] | None = None,
    check_all: bool = False,
) -> list[ToolCheckResult]:
    """Probe the selected manifest tools and report each one's status.

    Tools the workspace config disables are reported as
    :attr:`ToolStatus.DISABLED` rather than dropped, so a caller can tell
    "turned off here" apart from "not installed" — the distinction the whole
    report exists to make.

    Args:
        registry: Loaded manifest registry.
        context: Runtime context used for install hints.
        config: Workspace config deciding tool enablement. ``None`` behaves
            like ``check_all``.
        tool_names: Explicit tool subset. When given, enablement is ignored:
            an operator naming a tool wants it probed. Names are looked up with
            ``registry.get``, which raises ``KeyError`` for an unknown tool;
            callers validate names before reaching here.
        check_all: Probe every tool regardless of config enablement.

    Returns:
        list[ToolCheckResult]: One result per selected tool, in manifest order.
    """
    if tool_names:
        return [
            check_tool(tool=registry.get(name), context=context) for name in tool_names
        ]

    all_tools = list(registry.all_tools(include_dev=True))
    if check_all or config is None:
        return [check_tool(tool=tool, context=context) for tool in all_tools]

    return [
        (
            check_tool(tool=tool, context=context)
            if config.is_tool_enabled(tool.name)
            else ToolCheckResult(
                tool=tool,
                status=ToolStatus.DISABLED,
                install_hint="",
                upgrade_hint="",
            )
        )
        for tool in all_tools
    ]


def mcp_extra_status() -> dict[str, str]:
    """Return informational status for the optional ``lintro[mcp]`` extra.

    Missing MCP is reported but never treated as a doctor failure.

    Returns:
        Dict with ``name``, ``status``, ``message``, and ``hint`` keys.
    """
    from lintro.mcp import is_mcp_available

    if is_mcp_available():
        return {
            "name": "mcp",
            "status": ToolStatus.OK.value,
            "message": "Python mcp SDK installed (lintro[mcp])",
            "hint": "Start with: lintro mcp",
        }
    return {
        "name": "mcp",
        "status": ToolStatus.DISABLED.value,
        "message": "optional extra not installed",
        "hint": "uv pip install 'lintro[mcp]'",
    }


class DoctorCheckStatus(StrEnum):
    """Outcome of a single doctor check.

    Attributes:
        OK: The condition holds; nothing to do.
        WARNING: Degraded but usable — an outdated binary, a config
            inconsistency, a credential that could not be probed.
        ERROR: Something an operator must fix before the affected feature
            works at all.
        SKIPPED: The check does not apply to this environment (an optional
            extra that is not installed, AI checks with no AI feature
            enabled). Never counts against health.
    """

    OK = auto()
    WARNING = auto()
    ERROR = auto()
    SKIPPED = auto()


class DoctorCheckCategory(StrEnum):
    """Which part of the environment a doctor check covers.

    Attributes:
        CONFIG: Workspace configuration loading and consistency.
        TOOLS: External tool binaries.
        AI: AI provider availability and authentication.
        EXTRAS: Optional lintro extras such as ``lintro[mcp]``.
    """

    CONFIG = auto()
    TOOLS = auto()
    AI = auto()
    EXTRAS = auto()


class DoctorHealth(StrEnum):
    """Overall verdict for a doctor report.

    Attributes:
        HEALTHY: Every applicable check passed.
        DEGRADED: At least one check reported a warning or an error. A single
            vocabulary covers both, because the actionable question an agent
            asks is "can I trust this environment as-is", and the per-check
            statuses already carry the severity.
    """

    HEALTHY = auto()
    DEGRADED = auto()


#: How a tool/AI :class:`ToolStatus` projects onto a doctor check status.
_STATUS_PROJECTION: Final[dict[ToolStatus, DoctorCheckStatus]] = {
    ToolStatus.OK: DoctorCheckStatus.OK,
    ToolStatus.MISSING: DoctorCheckStatus.ERROR,
    ToolStatus.INCOMPATIBLE: DoctorCheckStatus.ERROR,
    ToolStatus.OUTDATED: DoctorCheckStatus.WARNING,
    # "Ran, but lintro could not read a version out of it" is not proof of a
    # broken environment, so it must not read as one.
    ToolStatus.UNKNOWN: DoctorCheckStatus.WARNING,
    ToolStatus.DISABLED: DoctorCheckStatus.SKIPPED,
}


@dataclass(frozen=True)
class DoctorCheck:
    """One actionable statement about the environment's health.

    Attributes:
        check: Stable machine-readable identifier (``tools.missing``,
            ``ai.cli.claude``). Agents branch on this, so renaming one is a
            breaking change.
        status: The outcome.
        detail: Human-readable description of what was found.
        remediation: The command or config change that resolves it. Empty when
            the check passed or nothing can be done.
        category: Which part of the environment the check covers.
    """

    check: str
    status: DoctorCheckStatus
    detail: str
    remediation: str = ""
    category: DoctorCheckCategory = DoctorCheckCategory.TOOLS

    def to_dict(self) -> dict[str, Any]:
        """Serialize the check to a JSON-compatible dict.

        Returns:
            dict[str, Any]: The check's wire representation.
        """
        return {
            "check": self.check,
            "status": self.status.value,
            "detail": self.detail,
            "remediation": self.remediation,
            "category": self.category.value,
        }


@dataclass(frozen=True)
class DoctorReport:
    """A full environment health report.

    Attributes:
        checks: Every check that was run, in report order.
    """

    checks: tuple[DoctorCheck, ...]

    @property
    def health(self) -> DoctorHealth:
        """Return the overall verdict.

        Returns:
            DoctorHealth: ``DEGRADED`` when any check warned or errored.
        """
        degraded = (DoctorCheckStatus.WARNING, DoctorCheckStatus.ERROR)
        if any(check.status in degraded for check in self.checks):
            return DoctorHealth.DEGRADED
        return DoctorHealth.HEALTHY

    def summary(self) -> dict[str, int]:
        """Count the checks by status.

        Returns:
            dict[str, int]: One count per :class:`DoctorCheckStatus` member,
            plus ``total``.
        """
        counts = {
            status.value: sum(1 for c in self.checks if c.status is status)
            for status in DoctorCheckStatus
        }
        counts["total"] = len(self.checks)
        return counts

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to a JSON-compatible dict.

        Returns:
            dict[str, Any]: Mapping with ``health``, ``checks``, ``summary``.
        """
        return {
            "health": self.health.value,
            "checks": [check.to_dict() for check in self.checks],
            "summary": self.summary(),
        }


def _config_checks() -> tuple[LintroConfig | None, list[DoctorCheck]]:
    """Load the workspace config and report on it.

    Returns:
        tuple: The loaded config (``None`` when loading failed) and its checks.
    """
    from lintro.config.config_loader import get_config
    from lintro.utils.config_validation import validate_config_consistency

    try:
        config = get_config(reload=True)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - any parse failure is a report line, not a crash
        return None, [
            DoctorCheck(
                category=DoctorCheckCategory.CONFIG,
                check="config.load",
                status=DoctorCheckStatus.ERROR,
                detail=f"Configuration could not be loaded: {exc}",
                remediation=(
                    "Fix the syntax of .lintro-config.yaml (or [tool.lintro] "
                    "in pyproject.toml), then re-run lintro doctor"
                ),
            ),
        ]

    source = config.config_path or "built-in defaults (no config file found)"
    checks = [
        DoctorCheck(
            category=DoctorCheckCategory.CONFIG,
            check="config.load",
            status=DoctorCheckStatus.OK,
            detail=f"Configuration loaded from {source}",
        ),
    ]

    try:
        warnings = validate_config_consistency()
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a broken native config must not abort the report
        warnings = [f"consistency check failed: {exc}"]

    if warnings:
        checks.append(
            DoctorCheck(
                category=DoctorCheckCategory.CONFIG,
                check="config.consistency",
                status=DoctorCheckStatus.WARNING,
                detail="; ".join(warnings),
                remediation=(
                    "Align the native tool configs with lintro's line_length, "
                    "or run lintro config to inspect the effective settings"
                ),
            ),
        )
    else:
        checks.append(
            DoctorCheck(
                category=DoctorCheckCategory.CONFIG,
                check="config.consistency",
                status=DoctorCheckStatus.OK,
                detail="No conflicting settings between lintro and native configs",
            ),
        )
    return config, checks


def _tool_checks(*, config: LintroConfig | None) -> list[DoctorCheck]:
    """Probe every enabled tool and fold the results into two checks.

    One record per tool would put ~40 near-duplicate entries in front of an
    agent that already has ``lintro_list_tools`` for the per-tool view. What
    the report adds is the verdict: is anything missing, is anything too old.

    Args:
        config: Workspace config, or ``None`` when it failed to load.

    Returns:
        list[DoctorCheck]: The ``tools.missing`` and ``tools.versions`` checks.
    """
    from lintro.tools.core.install_context import RuntimeContext

    registry = ManifestRegistry.load()
    results = collect_tool_checks(
        registry=registry,
        context=RuntimeContext.detect(),
        config=config,
    )
    # Dev-tier tools are optional by construction; the CLI already excludes
    # them from its failure counts and the report must agree.
    production = [
        result
        for result in results
        if result.tool.tier != "dev" and result.status is not ToolStatus.DISABLED
    ]
    missing = [r for r in production if r.status is ToolStatus.MISSING]
    incompatible = [r for r in production if r.status is ToolStatus.INCOMPATIBLE]
    outdated = [r for r in production if r.status is ToolStatus.OUTDATED]
    unreadable = [r for r in production if r.status is ToolStatus.UNKNOWN]
    # Enablement is only applied when a config was loaded; saying "enabled"
    # after a failed load would claim a filter that never ran.
    scope = "enabled tool(s)" if config is not None else "tool(s)"

    checks: list[DoctorCheck] = []
    if missing:
        names = sorted(r.tool.name for r in missing)
        checks.append(
            DoctorCheck(
                category=DoctorCheckCategory.TOOLS,
                check="tools.missing",
                status=DoctorCheckStatus.ERROR,
                detail=f"{len(names)} {scope} not installed: " + ", ".join(names),
                remediation=f"lintro install {' '.join(names)}",
            ),
        )
    else:
        checks.append(
            DoctorCheck(
                category=DoctorCheckCategory.TOOLS,
                check="tools.missing",
                status=DoctorCheckStatus.OK,
                detail=f"All {len(production)} {scope} are installed",
            ),
        )

    if incompatible or outdated or unreadable:
        upgradable = sorted(r.tool.name for r in (*incompatible, *outdated))
        parts: list[str] = []
        if incompatible:
            parts.append(
                "below the required minimum: "
                + ", ".join(sorted(r.tool.name for r in incompatible)),
            )
        if outdated:
            parts.append(
                "outdated: " + ", ".join(sorted(r.tool.name for r in outdated)),
            )
        if unreadable:
            parts.append(
                "version unreadable: "
                + ", ".join(sorted(r.tool.name for r in unreadable)),
            )
        checks.append(
            DoctorCheck(
                category=DoctorCheckCategory.TOOLS,
                check="tools.versions",
                # A version below the hard minimum is not "a bit old": lintro
                # skips such a tool outright, so it must not read as a warning
                # when the same status is an error everywhere else.
                status=(
                    DoctorCheckStatus.ERROR
                    if incompatible
                    else DoctorCheckStatus.WARNING
                ),
                detail="; ".join(parts),
                remediation=(
                    f"lintro install --upgrade {' '.join(upgradable)}"
                    if upgradable
                    else "Run the tool manually to see what it reports"
                ),
            ),
        )
    else:
        checks.append(
            DoctorCheck(
                category=DoctorCheckCategory.TOOLS,
                check="tools.versions",
                status=DoctorCheckStatus.OK,
                detail="Every installed tool meets its minimum version",
            ),
        )
    return checks


def _ai_checks(*, config: LintroConfig | None) -> list[DoctorCheck]:
    """Report AI provider availability and authentication.

    Args:
        config: Workspace config, or ``None`` when it failed to load.

    Returns:
        list[DoctorCheck]: One check per AI probe, or a single skipped record
        when no AI feature is enabled or the ``[ai]`` extra is absent.
    """
    if config is None:
        return [
            DoctorCheck(
                category=DoctorCheckCategory.AI,
                check="ai.provider",
                status=DoctorCheckStatus.SKIPPED,
                detail="Configuration could not be loaded, so AI was not checked",
                remediation="Fix the configuration first",
            ),
        ]

    try:
        from lintro.ai.doctor_checks import check_ai_configuration
        from lintro.ai.interface import resolve_ai_config
    except ImportError as exc:
        return [
            DoctorCheck(
                category=DoctorCheckCategory.AI,
                check="ai.extra",
                status=DoctorCheckStatus.SKIPPED,
                detail=f"AI support is not installed: {exc}",
                remediation="uv pip install 'lintro[ai]'",
            ),
        ]

    try:
        results = check_ai_configuration(resolve_ai_config(config))
    except Exception as exc:  # noqa: BLE001 - a probe that blew up is a report line
        # A malformed ``ai:`` block or a provider whose presence check raises
        # must not take the whole report down with it: the caller asked what is
        # wrong with the environment, and this is an answer.
        return [
            DoctorCheck(
                category=DoctorCheckCategory.AI,
                check="ai.provider",
                status=DoctorCheckStatus.WARNING,
                detail=f"AI configuration could not be checked: {exc}",
                remediation="Review the ai.* settings, then re-run lintro doctor",
            ),
        ]

    if not results:
        return [
            DoctorCheck(
                category=DoctorCheckCategory.AI,
                check="ai.provider",
                status=DoctorCheckStatus.SKIPPED,
                detail="No AI feature is enabled (ai.lint / ai.review)",
                remediation=(
                    "Enable ai.review and set ai.transport in the workspace config"
                ),
            ),
        ]
    return [
        DoctorCheck(
            category=DoctorCheckCategory.AI,
            check=result.name,
            status=_STATUS_PROJECTION.get(result.status, DoctorCheckStatus.WARNING),
            detail=result.message,
            remediation=result.hint,
        )
        for result in results
    ]


def _extras_checks() -> list[DoctorCheck]:
    """Report availability of lintro's optional extras.

    Returns:
        list[DoctorCheck]: The ``extras.mcp`` check. Never an error: the MCP
        SDK being absent is a fact about the install, not a fault.
    """
    info = mcp_extra_status()
    installed = info["status"] == ToolStatus.OK.value
    return [
        DoctorCheck(
            category=DoctorCheckCategory.EXTRAS,
            check="extras.mcp",
            status=(DoctorCheckStatus.OK if installed else DoctorCheckStatus.SKIPPED),
            detail=info["message"],
            remediation=info["hint"],
        ),
    ]


def collect_doctor_report() -> DoctorReport:
    """Run every doctor check against the current working directory.

    The workspace is the process cwd, the same anchor ``lintro doctor`` uses:
    the config file, native tool configs, and ``PATH`` are all resolved from
    there. Callers that need a different root (the MCP server) chdir first.

    Returns:
        DoctorReport: Config, tool, AI, and extras checks.
    """
    config, checks = _config_checks()
    checks.extend(_tool_checks(config=config))
    checks.extend(_ai_checks(config=config))
    checks.extend(_extras_checks())
    return DoctorReport(checks=tuple(checks))
