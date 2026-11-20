"""Capability service boundary for consumers below the tool/plugin layer.

The tool package registers its probe provider during initialization. Consumers
can share snapshots and invalidate caches without importing the tool layer.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Protocol

from lintro.models.core.tool_outcome import Skipped, ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.utils.tool_metadata import VERSION_LAG_KEY, ToolNote, with_tool_notes


class SnapshotProvider(Protocol):
    """Probe and cache operations supplied by the initialized tool layer."""

    def probe_all_tools(
        self,
        *,
        force: bool = False,
        cache_root: Path | None = None,
        tool_names: list[str] | None = None,
        for_introspection: bool = False,
    ) -> dict[str, ToolSnapshot]:
        """Probe a set of tools through the shared cache.

        Args:
            force: Ignore valid cached entries.
            cache_root: Execution and cache directory.
            tool_names: Requested registry names, or all registered tools.
            for_introspection: Report registry fallbacks without running them.

        Returns:
            Snapshot map for the requested tools.
        """
        ...

    def get_tool_snapshot(
        self,
        tool_name: str,
        *,
        force: bool = False,
        cache_root: Path | None = None,
    ) -> ToolSnapshot:
        """Read one tool's capability evidence.

        Args:
            tool_name: Registry name to inspect.
            force: Ignore valid cached entries.
            cache_root: Execution and cache directory.

        Returns:
            Cached or fresh evidence for this tool.
        """
        ...

    def clear_snapshot_cache(
        self,
        *,
        cache_root: Path | None = None,
        include_disk: bool = True,
    ) -> None:
        """Invalidate stored capability evidence.

        Args:
            cache_root: Directory whose disk cache should be removed.
            include_disk: Whether disk entries should also be removed.
        """
        ...

    def set_force_fresh_probes(self, force: bool) -> None:
        """Invalidate memory once and bypass disk caches until reset.

        Args:
            force: Whether to force a fresh probe cycle.
        """
        ...

    def is_strict_missing_tools(self) -> bool:
        """Read the execution policy for missing tools.

        Returns:
            Whether unavailable tools should fail the run.
        """
        ...

    def snapshot_to_unavailable_result(
        self,
        snapshot: ToolSnapshot,
        *,
        strict: bool | None = None,
    ) -> ToolResult:
        """Convert unavailable evidence to a frozen execution result.

        Args:
            snapshot: Tool capability evidence.
            strict: Override for the missing-tool exit policy.

        Returns:
            Frozen result with the probe diagnosis and no invented counts.
        """
        ...


_provider: SnapshotProvider | None = None


def register_snapshot_provider(provider: SnapshotProvider) -> None:
    """Connect the initialized tool layer to its lower-layer consumers.

    Args:
        provider: Tool package's shared probe and cache operations.
    """
    global _provider
    _provider = provider


def _get_provider() -> SnapshotProvider:
    """Require tool initialization before probing registered tools.

    Returns:
        Registered tool capability provider.

    Raises:
        RuntimeError: If the tool package has not initialized its provider.
    """
    if _provider is None:
        raise RuntimeError("Tool capability provider has not been initialized")
    return _provider


def lookup_snapshot(
    snapshots: Mapping[str, ToolSnapshot],
    name: str,
) -> ToolSnapshot | None:
    """Return a snapshot for ``name``, trying hyphen and underscore aliases.

    Args:
        snapshots: Map keyed by tool name.
        name: Requested tool name (manifest or registry spelling).

    Returns:
        Matching snapshot, or None when no alias hits.
    """
    from lintro.enums.tool_name import tool_name_aliases

    for candidate in tool_name_aliases(name):
        snap = snapshots.get(candidate)
        if snap is not None:
            return snap
    return None


def probe_all_tools(
    *,
    force: bool = False,
    cache_root: Path | None = None,
    tool_names: list[str] | None = None,
    for_introspection: bool = False,
) -> dict[str, ToolSnapshot]:
    """Read capability evidence through the registered tool provider.

    Args:
        force: Ignore valid cached entries.
        cache_root: Execution and cache directory.
        tool_names: Requested names, or all registered tools.
        for_introspection: Report registry fallbacks without running them.

    Returns:
        Snapshot map for the requested tools.
    """
    return _get_provider().probe_all_tools(
        force=force,
        cache_root=cache_root,
        tool_names=tool_names,
        for_introspection=for_introspection,
    )


def get_tool_snapshot(
    tool_name: str,
    *,
    force: bool = False,
    cache_root: Path | None = None,
) -> ToolSnapshot:
    """Read one tool's evidence from the shared provider.

    Args:
        tool_name: Registered name to inspect.
        force: Ignore valid cached entries.
        cache_root: Execution and cache directory.

    Returns:
        Cached or fresh tool evidence.
    """
    return _get_provider().get_tool_snapshot(
        tool_name,
        force=force,
        cache_root=cache_root,
    )


def clear_snapshot_cache(
    *,
    cache_root: Path | None = None,
    include_disk: bool = True,
) -> None:
    """Clear capability evidence if the tool layer has been initialized.

    Args:
        cache_root: Directory whose disk cache should be removed.
        include_disk: Whether disk entries should also be removed.
    """
    if _provider is not None:
        _provider.clear_snapshot_cache(cache_root=cache_root, include_disk=include_disk)


def set_force_fresh_probes(force: bool) -> None:
    """Invalidate memory once and bypass disk caches until reset.

    Args:
        force: Whether the next cycle must ignore cached entries.
    """
    if _provider is not None:
        _provider.set_force_fresh_probes(force)


def is_strict_missing_tools() -> bool:
    """Read the missing-tool exit policy from the tool provider.

    Returns:
        Whether missing tools must fail this run.
    """
    return _get_provider().is_strict_missing_tools()


def snapshot_to_unavailable_result(
    snapshot: ToolSnapshot,
    *,
    strict: bool | None = None,
) -> ToolResult:
    """Create a frozen unavailable result without inventing measurements.

    Args:
        snapshot: Capability evidence carrying the probe diagnosis.
        strict: Override for the missing-tool policy.

    Returns:
        Frozen unavailable execution result.
    """
    return _get_provider().snapshot_to_unavailable_result(snapshot, strict=strict)


def strict_failure_reason(reason: str, hint: str | None) -> str:
    """Append the install hint to the reason a strict run fails on.

    The reason is what the summary row and ``unavailable_reason`` show, so a
    strict failure carries its own remedy there instead of only in the tool's
    log output (#3371).

    Args:
        reason: Why the tool could not run.
        hint: Install or upgrade guidance, if the probe has any.

    Returns:
        The reason, followed by the hint when one exists.
    """
    if not hint or hint in reason:
        return reason
    return f"{reason.rstrip().rstrip('.')}. {hint}"


def below_minimum_result(
    *,
    name: str,
    reason: str,
    detail: str,
    hint: str | None = None,
    strict: bool | None = None,
) -> ToolResult:
    """Build the result for an installed tool older than lintro's minimum.

    By default the tool is skipped and the run is unaffected. Under the
    missing-tool policy (``execution.strict_missing_tools`` or
    ``--strict-missing-tools``) an outdated binary is as unusable as an absent
    one, so it takes the same strict ``Unavailable`` outcome: the run fails,
    no finding is invented, and the reason carries the upgrade hint (#3371).

    Args:
        name: Tool name the result reports under.
        reason: Why the version gate refused the binary.
        detail: Human-readable explanation, including the install or upgrade
            hint. It is prefixed ``Skipping <name>:`` for a skip and
            ``<name> unavailable:`` for a strict failure, so the output agrees
            with the row's status.
        hint: Install or upgrade guidance appended to a strict failure reason.
        strict: Override for the missing-tool policy; None reads configuration.

    Returns:
        A skipped result, or a strict unavailable result that fails the run.
    """
    strict_policy = is_strict_missing_tools() if strict is None else strict
    if strict_policy:
        outcome = ToolOutcome(
            execution=Unavailable(
                reason=strict_failure_reason(reason, hint),
                strict=True,
            ),
        )
        output = f"{name} unavailable: {detail}"
    else:
        outcome = ToolOutcome(execution=Skipped(reason))
        output = f"Skipping {name}: {detail}"
    return ToolResult.from_outcome(outcome, name=name, output=output)


def upgrade_command(name: str) -> str:
    """Return the command that upgrades a tool to the version lintro pins.

    Args:
        name: Tool name as lintro reports it.

    Returns:
        The ``lintro install --upgrade`` command for the tool.
    """
    return f"lintro install --upgrade {name}"


def known_bad_install_hint(pinned: str) -> str:
    """Say what to install instead of a known-bad release.

    ``lintro install --upgrade`` cannot replace a known-bad release yet
    (#3567), and an install hint's range ("v2.6.0+") can include the refused
    release, so the hint names only the version lintro tests.

    Args:
        pinned: The version lintro pins and tests, or empty when unknown.

    Returns:
        The hint, a sentence ending in a full stop.
    """
    if not pinned:
        return "Install the version lintro tests."
    return f"Install {pinned}, the version lintro tests."


_upgrade_suggested: set[str] = set()
_upgrade_suggested_lock = threading.Lock()


def claim_upgrade_suggestion(name: str) -> bool:
    """Record that a run is about to suggest upgrading ``name``.

    Tools can be version-checked more than once per run (one check per
    project root), and the suggestion is shown once per tool per run.

    Args:
        name: Tool name.

    Returns:
        True the first time per run for this tool, False afterwards.
    """
    with _upgrade_suggested_lock:
        if name in _upgrade_suggested:
            return False
        _upgrade_suggested.add(name)
        return True


_version_lag_used: dict[str, dict[str, str]] = {}


def record_version_lag(name: str, *, version: str, minimum: str) -> None:
    """Remember that the override let ``name`` run below its minimum this run.

    Args:
        name: Tool name.
        version: The installed version that ran.
        minimum: The minimum it is below.
    """
    with _upgrade_suggested_lock:
        _version_lag_used.setdefault(
            name,
            {
                "version": version,
                "minimum": minimum,
                "allowed_by": ALLOW_VERSION_LAG_ENV,
            },
        )


def with_version_lag_note(result: ToolResult) -> ToolResult:
    """Mark a result whose tool ran below its minimum under the override.

    A log line alone let such a run read as an ordinary pass; the note shows
    in the summary table and reports, and ``metadata.version_lag`` in JSON.

    Args:
        result: One tool's result from this run.

    Returns:
        The result with the note and ``version_lag`` metadata, or unchanged
        when the override did not apply to the tool.
    """
    with _upgrade_suggested_lock:
        lag = _version_lag_used.get(result.name)
    if lag is None or result.skipped or result.unavailable:
        return result
    note = ToolNote(
        summary="below minimum, allowed by override",
        message=(
            f"{result.name} {lag['version']} is below the minimum "
            f"{lag['minimum']} and ran only because {ALLOW_VERSION_LAG_ENV} "
            "allows it"
        ),
    )
    metadata = with_tool_notes(result.metadata, [note])
    metadata[VERSION_LAG_KEY] = dict(lag)
    return replace(result, metadata=metadata)


def reset_upgrade_suggestions() -> None:
    """Start a new run: forget the last run's upgrade suggestions and lag notes."""
    with _upgrade_suggested_lock:
        _upgrade_suggested.clear()
        _version_lag_used.clear()


ALLOW_VERSION_LAG_ENV: str = "LINTRO_ALLOW_VERSION_LAG"


class VersionLagPolicy(Enum):
    """Explicit wildcard policy for below-minimum tool versions."""

    ALLOW_ALL = "*"


def _parse_allow_version_lag(raw: str | None) -> set[str] | VersionLagPolicy:
    """Parse ``LINTRO_ALLOW_VERSION_LAG`` into a tool-name set.

    Args:
        raw: Env value: comma-separated tool names, ``*`` for all, or empty.

    Returns:
        A set of lowercased tool names, ``VersionLagPolicy.ALLOW_ALL`` when
        ``*``, or an empty set when unset/blank (full enforcement).
    """
    if raw is None:
        return set()
    text = raw.strip()
    if not text:
        return set()
    if text == "*":
        return VersionLagPolicy.ALLOW_ALL
    names = {part.strip().lower() for part in text.split(",") if part.strip()}
    return names


def _version_lag_allowed(tool_name: str) -> bool:
    """Return whether ``tool_name`` may run when below the minimum version.

    Args:
        tool_name: Tool name from the tool definition.

    Returns:
        True when the tool is listed in ``LINTRO_ALLOW_VERSION_LAG`` (or ``*``).
    """
    allow = _parse_allow_version_lag(os.environ.get(ALLOW_VERSION_LAG_ENV))
    if isinstance(allow, VersionLagPolicy):
        return True
    return tool_name.lower() in allow


def snapshot_is_runnable(snapshot: ToolSnapshot) -> bool:
    """Apply the execution gate, including allowed version lag and module commands.

    Args:
        snapshot: Capability evidence produced by the tool probe.

    Returns:
        Whether execution should proceed with this snapshot.
    """
    # The override tolerates a tool older than the minimum while images lag;
    # it never lets a known-bad release run (#3556).
    return snapshot.available and (
        snapshot.version_check_passed
        or (
            snapshot.version is not None
            and not snapshot.known_bad
            and _version_lag_allowed(snapshot.name)
        )
    )
