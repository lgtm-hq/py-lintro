"""The ``lintro_list_tools``, ``lintro_versions``, and ``lintro_doctor`` tools.

An agent deciding whether to call ``lintro_check`` needs to know what lintro
can actually do *here*: which tools exist, which of them are installed, and
whether the environment is healthy enough to trust the answer. Without that it
either parses the human-facing ``lintro ls`` / ``lintro versions`` /
``lintro doctor`` text or discovers the truth by failing a call (issue #1240).

All three tools are read-only and idempotent: nothing is written, no provider
is called, and running one twice costs only the subprocess probes.

Two conventions carry over from the toolkits that landed before this one:

* **Degraded but visible.** A tool that is not installed is listed with
  ``installed: false`` and its install hint rather than omitted. An absent
  entry is indistinguishable from a tool lintro never supported, which is
  exactly the confusion that makes an agent give up on a capability it could
  have asked the operator to install.
* **No heavy imports at import time.** Only the registry types are imported
  here; the plugin registry, the manifest, the version probes, and the doctor
  data layer are all pulled inside the handlers, so registering this toolkit
  costs nothing.

Both remaining shapes are projections of the same two sources — the live
plugin registry (what lintro can run) and ``manifest.json`` (what each tool
is, which version is expected, which install profiles include it) — joined on
the tool name. Plugin names spell separators with ``-`` and manifest names with
``_`` (``astro-check`` / ``astro_check``), so the join normalizes before
matching rather than silently dropping four tools.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from lintro.mcp.registry import McpToolSpec
from lintro.mcp.toolkits.runner import workspace_session

if TYPE_CHECKING:
    from lintro.enums.tool_type import ToolType
    from lintro.plugins.protocol import ToolDefinition
    from lintro.tools.core.tool_registry import ManifestRegistry

__all__ = ["INTROSPECTION_TIMEOUT_SECONDS", "build_introspection_toolkit"]

_EMPTY_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

# Every one of these tools probes each external binary for its version, and a
# tool that hangs is allowed up to its own probe timeout, so the worst case
# scales with the size of the manifest and blows past the foundation's 300s
# default on a machine with slow or wedged binaries. The budget still exists:
# a wedged probe must not hold the JSON-RPC stream open forever.
INTROSPECTION_TIMEOUT_SECONDS: Final[float] = 900.0

#: Capability label reported for advisory AI finders, which run under
#: ``lintro review`` rather than ``chk``/``fmt``. Mirrors
#: ``lintro.cli_utils.commands.list_tools.ADVISORY_CAPABILITY``.
ADVISORY_CAPABILITY: Final[str] = "review"

#: Profile strategies whose membership is fixed by the manifest alone. The
#: others (``auto-detect``, ``filter``) resolve against whatever languages are
#: detected in a workspace, so a tool's membership in them is not a property of
#: the tool and is reported on the profile instead of on every tool.
_STATIC_PROFILE_STRATEGIES: Final[frozenset[str]] = frozenset({"explicit", "all"})

_LIST_TOOLS_DESCRIPTION: Final[str] = (
    "List every lintro tool with its type, languages, capabilities, execution "
    "class, install profiles, and whether its binary is installed in this "
    "workspace (with the detected and expected versions). Tools that are not "
    "installed are listed with installed=false and an install hint rather "
    "than omitted. Read-only: nothing is executed beyond version probes."
)

_VERSIONS_DESCRIPTION: Final[str] = (
    "Report the installed version of every external tool against the minimum "
    "and recommended versions lintro expects, flagging each as ok, outdated, "
    "or missing. Read-only."
)

_DOCTOR_DESCRIPTION: Final[str] = (
    "Run lintro's environment health checks and return them as structured "
    "records ({check, status, detail, remediation}) covering configuration "
    "validity, tool binaries, AI provider availability and authentication, "
    "and optional extras, plus an overall healthy/degraded verdict. Read-only: "
    "no provider call is made and nothing is written."
)


def _profile_membership(*, manifest: ManifestRegistry) -> dict[str, tuple[str, ...]]:
    """Map each manifest tool to the install profiles that always contain it.

    Args:
        manifest: Loaded manifest registry.

    Returns:
        dict[str, tuple[str, ...]]: Tool name to sorted profile names. Only
        statically-resolvable profiles are considered; see
        :data:`_STATIC_PROFILE_STRATEGIES`.
    """
    membership: dict[str, set[str]] = {
        tool.name: set() for tool in manifest.all_tools(include_dev=True)
    }
    for name, profile in manifest.profiles.items():
        if profile.strategy == "all":
            for tool_name in membership:
                membership[tool_name].add(name)
        elif profile.strategy == "explicit":
            for tool_name in profile.tools:
                if tool_name in membership:
                    membership[tool_name].add(name)
    return {name: tuple(sorted(names)) for name, names in membership.items()}


def _profiles_payload(*, manifest: ManifestRegistry) -> list[dict[str, Any]]:
    """Describe every install profile the manifest defines.

    Args:
        manifest: Loaded manifest registry.

    Returns:
        list[dict[str, Any]]: One entry per profile, marked ``static`` when its
        membership is fixed and ``workspace`` when it depends on the languages
        detected in the tree.
    """
    return [
        {
            "name": name,
            "description": profile.description,
            "strategy": profile.strategy,
            "resolution": (
                "static"
                if profile.strategy in _STATIC_PROFILE_STRATEGIES
                else "workspace"
            ),
        }
        for name, profile in sorted(manifest.profiles.items())
    ]


def _tool_types(*, tool_type: ToolType) -> list[str]:
    """Decompose a :class:`~lintro.enums.tool_type.ToolType` bitmask.

    Reported as a list rather than the single ``type`` field the issue sketched:
    ``ToolType`` is a ``Flag`` and several tools are genuinely both a linter and
    a formatter (ruff, oxlint), so a scalar would have to pick one and lie.

    Args:
        tool_type: The definition's ``tool_type`` flag value.

    Returns:
        list[str]: Lowercased flag names, in declaration order.
    """
    from lintro.enums.tool_type import ToolType

    return [
        member.name.lower()
        for member in ToolType
        if member.name is not None and member in tool_type
    ]


def _capabilities(
    *,
    name: str,
    definition: ToolDefinition,
    check_tools: frozenset[str],
    fix_tools: frozenset[str],
) -> list[str]:
    """Resolve the verbs a tool can be invoked with.

    Advisory AI finders report ``review``: they are excluded from ``chk`` and
    ``fmt`` so their nondeterministic findings never gate deterministic checks
    (issue #1308), which also makes them unreachable from ``lintro_check``.

    Args:
        name: Registered tool name.
        definition: The plugin's ``ToolDefinition``.
        check_tools: Names of tools that support checking, resolved once for
            the whole listing rather than per tool.
        fix_tools: Names of tools that support fixing.

    Returns:
        list[str]: Capability labels in display order.
    """
    from lintro.enums.action import Action

    if definition.is_advisory:
        return [ADVISORY_CAPABILITY]
    capabilities: list[str] = []
    if name in check_tools:
        capabilities.append(Action.CHECK.value)
    if name in fix_tools:
        capabilities.append(Action.FIX.value)
    return capabilities


def _list_tools_payload() -> dict[str, Any]:
    """Build the ``lintro_list_tools`` result.

    Must be called inside :func:`~lintro.mcp.toolkits.runner.workspace_session`:
    tool enablement and the plugin registry are resolved against the workspace
    configuration.

    Returns:
        dict[str, Any]: Mapping with ``tools``, ``profiles``, and ``summary``.
    """
    from lintro.plugins.registry import ToolRegistry
    from lintro.tools import tool_manager
    from lintro.tools.core.install_context import RuntimeContext
    from lintro.tools.core.tool_registry import ManifestRegistry
    from lintro.utils import doctor_report

    manifest = ManifestRegistry.load()
    context = RuntimeContext.detect()
    membership = _profile_membership(manifest=manifest)
    plugins = tool_manager.get_all_tools()
    check_tools = frozenset(tool_manager.get_check_tools())
    fix_tools = frozenset(tool_manager.get_fix_tools())

    entries: list[dict[str, Any]] = []
    for name in sorted(plugins):
        definition = plugins[name].definition
        manifest_tool = manifest.get_or_none(name) or manifest.get_or_none(
            name.replace("-", "_"),
        )
        # An advisory finder (idiom-review) has no binary and no manifest entry,
        # so there is nothing to probe. It is still listed — with
        # installed=false — because "lintro knows this tool but cannot run it
        # as a binary" is what the caller needs to see.
        probe = (
            doctor_report.check_tool(tool=manifest_tool, context=context)
            if manifest_tool is not None
            else None
        )
        entries.append(
            {
                "name": name,
                "description": definition.description,
                "types": _tool_types(tool_type=definition.tool_type),
                "languages": (list(manifest_tool.languages) if manifest_tool else []),
                "installed": probe.installed if probe else False,
                "version": probe.installed_version if probe else None,
                "expected_version": manifest_tool.version if manifest_tool else None,
                "minimum_version": (
                    manifest_tool.min_version if manifest_tool else None
                ),
                "status": str(probe.status) if probe else "unknown",
                "can_fix": definition.can_fix,
                "capabilities": _capabilities(
                    name=name,
                    definition=definition,
                    check_tools=check_tools,
                    fix_tools=fix_tools,
                ),
                "execution_class": definition.execution_class.value,
                "origin": ToolRegistry.get_origin(name),
                "profile_membership": list(
                    membership.get(manifest_tool.name, ()) if manifest_tool else (),
                ),
                "install_hint": probe.install_hint if probe else "",
            },
        )

    installed = sum(1 for entry in entries if entry["installed"])
    return {
        "tools": entries,
        "profiles": _profiles_payload(manifest=manifest),
        "summary": {
            "total": len(entries),
            "installed": installed,
            "missing": len(entries) - installed,
        },
    }


def _version_status(*, info: Any) -> str:
    """Classify one tool's version check.

    Args:
        info: A ``ToolVersionInfo`` from the version-checking machinery.

    Returns:
        str: ``missing`` when nothing usable answered the probe (the binary is
        absent, exited non-zero, or printed output no parser recognized — in
        every case the version is unknown and the tool cannot be relied on, and
        ``error`` carries the reason), ``outdated`` when the installed version
        is below the minimum lintro requires, ``ok`` otherwise.
    """
    from lintro.enums.tool_status import ToolStatus

    if info.current_version is None:
        return ToolStatus.MISSING.value
    if not info.version_check_passed:
        return ToolStatus.OUTDATED.value
    return ToolStatus.OK.value


def _versions_payload() -> dict[str, Any]:
    """Build the ``lintro_versions`` result.

    Must be called inside :func:`~lintro.mcp.toolkits.runner.workspace_session`.

    Returns:
        dict[str, Any]: Mapping with ``tools`` and ``summary``.
    """
    from lintro.tools.core.version_requirements import get_all_tool_versions

    entries: list[dict[str, Any]] = []
    for name, info in sorted(get_all_tool_versions().items()):
        status = _version_status(info=info)
        entries.append(
            {
                "name": name,
                "installed_version": info.current_version,
                "minimum_version": info.min_version,
                "recommended_version": info.recommended_version or None,
                "satisfies_minimum": info.version_check_passed,
                "below_recommended": info.below_recommended,
                "status": status,
                "error": info.error_message,
                "install_hint": info.install_hint,
            },
        )

    counts: dict[str, int] = {}
    for entry in entries:
        key = str(entry["status"])
        counts[key] = counts.get(key, 0) + 1
    counts["total"] = len(entries)
    return {"tools": entries, "summary": counts}


def _handler(
    *,
    workspace: Path,
    build: Callable[[], dict[str, Any]],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Bind a payload builder to ``workspace`` as an MCP handler.

    The session anchors cwd — the workspace config decides which tools are
    enabled, and native tool configs are resolved relative to it — and
    serializes the call against the lint tools, which mutate the same
    process-global state.

    Args:
        workspace: Workspace root the introspection is anchored to.
        build: Zero-argument payload builder to run inside the session.

    Returns:
        Callable: A handler accepting (and ignoring) an arguments dict.
    """

    def handler(_arguments: dict[str, Any]) -> dict[str, Any]:
        with workspace_session(workspace=workspace):
            return build()

    return handler


def _doctor_payload() -> dict[str, Any]:
    """Build the ``lintro_doctor`` result.

    Returns:
        dict[str, Any]: The serialized
        :class:`~lintro.utils.doctor_report.DoctorReport`.
    """
    from lintro.utils import doctor_report

    return doctor_report.collect_doctor_report().to_dict()


def build_introspection_toolkit(*, workspace: Path) -> tuple[McpToolSpec, ...]:
    """Build the introspection tool specifications.

    Args:
        workspace: Workspace root the tools report on.

    Returns:
        tuple[McpToolSpec, ...]: ``lintro_list_tools``, ``lintro_versions``,
        and ``lintro_doctor``.
    """
    return (
        McpToolSpec(
            name="lintro_list_tools",
            description=_LIST_TOOLS_DESCRIPTION,
            input_schema=dict(_EMPTY_INPUT_SCHEMA),
            handler=_handler(workspace=workspace, build=_list_tools_payload),
            read_only=True,
            destructive=False,
            idempotent=True,
            timeout_seconds=INTROSPECTION_TIMEOUT_SECONDS,
        ),
        McpToolSpec(
            name="lintro_versions",
            description=_VERSIONS_DESCRIPTION,
            input_schema=dict(_EMPTY_INPUT_SCHEMA),
            handler=_handler(workspace=workspace, build=_versions_payload),
            read_only=True,
            destructive=False,
            idempotent=True,
            timeout_seconds=INTROSPECTION_TIMEOUT_SECONDS,
        ),
        McpToolSpec(
            name="lintro_doctor",
            description=_DOCTOR_DESCRIPTION,
            input_schema=dict(_EMPTY_INPUT_SCHEMA),
            handler=_handler(workspace=workspace, build=_doctor_payload),
            read_only=True,
            destructive=False,
            idempotent=True,
            timeout_seconds=INTROSPECTION_TIMEOUT_SECONDS,
        ),
    )
