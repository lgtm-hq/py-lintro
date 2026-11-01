"""Capability service boundary for consumers below the tool/plugin layer.

The tool package registers its probe provider during initialization. Consumers
can share snapshots and invalidate caches without importing the tool layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from lintro.models.core.tool_result import ToolResult
from lintro.models.core.tool_snapshot import ToolSnapshot


class SnapshotProvider(Protocol):
    """Probe and cache operations supplied by the initialized tool layer."""

    def probe_all_tools(
        self,
        *,
        force: bool = False,
        cache_root: Path | None = None,
        tool_names: list[str] | None = None,
    ) -> dict[str, ToolSnapshot]:
        """Probe a set of tools through the shared cache.

        Args:
            force: Ignore valid cached entries.
            cache_root: Execution and cache directory.
            tool_names: Requested registry names, or all registered tools.

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
        """Choose whether the next probe must bypass its cache.

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
) -> dict[str, ToolSnapshot]:
    """Read capability evidence through the registered tool provider.

    Args:
        force: Ignore valid cached entries.
        cache_root: Execution and cache directory.
        tool_names: Requested names, or all registered tools.

    Returns:
        Snapshot map for the requested tools.
    """
    return _get_provider().probe_all_tools(
        force=force,
        cache_root=cache_root,
        tool_names=tool_names,
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
    """Control the next registered probe cycle.

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
