"""Cached capability probing for registered Lintro tools.

Probes each tool once (availability, version, capabilities), caches the
result under ``.lintro-cache/tool-snapshots.json`` keyed on binary path,
mtime, and lintro version, and exposes snapshots to check/format runners,
``list-tools``, and ``doctor``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import sysconfig
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from lintro import __version__
from lintro.enums.tool_name import normalize_tool_name
from lintro.models.core.tool_snapshot import ToolCapabilities as ToolCapabilities
from lintro.models.core.tool_snapshot import ToolSnapshot as ToolSnapshot
from lintro.tools.core.command_builders import CommandBuilderRegistry
from lintro.utils.tool_snapshots import (
    SnapshotProvider,
    register_snapshot_provider,
)
from lintro.utils.tool_snapshots import (
    lookup_snapshot as lookup_snapshot,
)

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.tools.core.tool_registry import ManifestTool
    from lintro.tools.core.version_parsing import ToolVersionInfo

# Default TTL for on-disk snapshot cache (seconds).
DEFAULT_SNAPSHOT_TTL_SECONDS: int = 600

# Relative cache path under the project / cwd root.
SNAPSHOT_CACHE_RELPATH: str = ".lintro-cache/tool-snapshots.json"

_cache_lock = threading.Lock()
_memory_cache: dict[str, ToolSnapshot] | None = None
_memory_cache_path: Path | None = None
_memory_probed_at: float | None = None
_force_fresh: bool = False


def _manifest_tool(name: str) -> ManifestTool | None:
    """Resolve version-only manifest entries that have no execution plugin.

    Args:
        name: Manifest or registry spelling of the tool.

    Returns:
        Matching manifest entry, or None when the name is unknown.
    """
    from lintro.enums.tool_name import tool_name_aliases
    from lintro.tools.core.tool_registry import ManifestRegistry

    registry = ManifestRegistry.load()
    for alias in tool_name_aliases(name):
        if alias in registry:
            return registry.get(alias)
    return None


def get_executable_command(
    tool_name: str,
    cwd: Path | None = None,
) -> list[str]:
    """Resolve the same command builders used by execution preparation.

    Args:
        tool_name: Registered name to resolve.
        cwd: Execution directory for project-local resolution.

    Returns:
        Command prefix selected for this directory.
    """
    from lintro.tools import tool_manager

    if not tool_manager.is_tool_registered(tool_name):
        manifest = _manifest_tool(tool_name)
        if manifest is not None and manifest.version_command:
            return list(manifest.version_command)
    try:
        normalized = normalize_tool_name(tool_name)
    except ValueError:
        normalized = None
    return CommandBuilderRegistry.get_command(tool_name, normalized, cwd)


def _resolved_binary(command: list[str]) -> str:
    """Identify the tool executable behind an interpreter command.

    Args:
        command: Resolved execution or version-probe prefix.

    Returns:
        Executable path whose mtime changes when the tool is upgraded.
    """
    if not command:
        return ""
    if len(command) >= 3 and command[1] == "-m" and command[0] == sys.executable:
        scripts_dir = sysconfig.get_path("scripts")
        module_binary = (
            shutil.which(command[2], path=scripts_dir) if scripts_dir else None
        )
        if module_binary:
            return module_binary
    return shutil.which(command[0]) or ""


def _is_cached_snapshot_fresh(snap: ToolSnapshot, *, search_root: Path) -> bool:
    """Return True when a cached snapshot should still be served.

    Available snapshots with a binary path are keyed on path + mtime.
    In-process tools (available, empty path) stay valid until TTL.
    Unavailable empty-path snapshots are re-checked with ``shutil.which`` on
    the resolved executable (``get_executable_command()[0]``) so a binary
    installed inside the TTL window is not served as missing. Alias-only PATH
    checks miss tools whose command name differs from the lintro tool name
    (e.g. ``markdownlint-cli2`` for ``markdownlint``).
    """
    command = get_executable_command(snap.name, cwd=search_root)
    if snap.command:
        resolved = _resolved_binary(command)
        if tuple(command) != snap.command or resolved != snap.binary_path:
            return False
    if snap.binary_path:
        if not Path(snap.binary_path).exists():
            return False
        return snap.cache_key_matches(
            binary_path=snap.binary_path,
            binary_mtime=_binary_mtime(snap.binary_path),
        )
    if snap.available:
        return True
    main_cmd = command[0] if command else snap.name
    return shutil.which(main_cmd) is None


def set_force_fresh_probes(force: bool) -> None:
    """Force the next probe cycle to ignore on-disk and memory caches.

    Args:
        force: When True, probes re-run even if a valid cache exists.
    """
    global _force_fresh
    with _cache_lock:
        _force_fresh = force


def clear_snapshot_cache(
    *,
    cache_root: Path | None = None,
    include_disk: bool = True,
) -> None:
    """Clear in-memory and optionally on-disk tool snapshot caches.

    Args:
        cache_root: Directory containing ``.lintro-cache``; defaults to cwd.
        include_disk: When False, only the in-process memory cache is reset.
    """
    global _memory_cache, _memory_cache_path, _memory_probed_at
    with _cache_lock:
        _memory_cache = None
        _memory_cache_path = None
        _memory_probed_at = None
        if not include_disk:
            return
        cache_file = _cache_file_path(cache_root=cache_root)
        if cache_file.exists():
            try:
                cache_file.unlink()
                logger.debug("Deleted tool snapshot cache: {}", cache_file)
            except OSError as exc:
                logger.warning(
                    "Could not delete snapshot cache {}: {}",
                    cache_file,
                    exc,
                )


def get_snapshot_ttl() -> int:
    """Return the configured snapshot TTL in seconds.

    Returns:
        TTL from execution config, or the default when config is unavailable.
    """
    try:
        from lintro.config.config_loader import get_config

        ttl = get_config().execution.tool_snapshot_ttl
        if ttl >= 1:
            return ttl
    except (ImportError, OSError, ValueError, AttributeError, RuntimeError):
        pass
    return DEFAULT_SNAPSHOT_TTL_SECONDS


def is_strict_missing_tools() -> bool:
    """Return whether missing tools should fail the run.

    Returns:
        True when ``execution.strict_missing_tools`` is enabled.
    """
    try:
        from lintro.config.config_loader import get_config

        return bool(get_config().execution.strict_missing_tools)
    except (ImportError, OSError, ValueError, AttributeError, RuntimeError):
        return False


def _cache_file_path(*, cache_root: Path | None = None) -> Path:
    """Resolve the on-disk snapshot cache path.

    Args:
        cache_root: Optional project root; defaults to the current working directory.

    Returns:
        Absolute path to ``tool-snapshots.json``.
    """
    root = cache_root if cache_root is not None else Path.cwd()
    return (root / SNAPSHOT_CACHE_RELPATH).resolve()


def _binary_mtime(path: str) -> float:
    """Return mtime for ``path``, or 0.0 when unavailable.

    Args:
        path: Filesystem path to the binary.

    Returns:
        Modification time as a float epoch seconds.
    """
    if not path:
        return 0.0
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return 0.0


def _find_native_config(native_configs: list[str], *, search_root: Path) -> bool:
    """Return True when any native config file exists under ``search_root``.

    Args:
        native_configs: Candidate config filenames / relative paths.
        search_root: Directory to search.

    Returns:
        True if at least one native config path exists.
    """
    for name in native_configs:
        candidate = search_root / name
        if candidate.exists():
            return True
    return False


def _unavailable_snapshot(
    name: str,
    *,
    probe_error: str,
    remediation_hint: str | None = None,
    binary_path: str = "",
    binary_mtime: float = 0.0,
    capabilities: ToolCapabilities | None = None,
    min_version: str = "",
    command: tuple[str, ...] = (),
) -> ToolSnapshot:
    """Build an unavailable snapshot with remediation context.

    Args:
        name: Tool name.
        probe_error: Why the tool is unavailable.
        remediation_hint: Optional install hint.
        binary_path: Path if partially resolved.
        binary_mtime: mtime if binary exists but probe failed.
        capabilities: Optional capability flags.
        min_version: Minimum version string for display.
        command: Command used for the failed probe.

    Returns:
        Unavailable ToolSnapshot.
    """
    return ToolSnapshot(
        name=name,
        available=False,
        version=None,
        capabilities=capabilities or ToolCapabilities(),
        probe_error=probe_error,
        remediation_hint=remediation_hint,
        binary_path=binary_path,
        binary_mtime=binary_mtime,
        version_check_passed=False,
        min_version=min_version,
        command=command,
    )


def probe_tool(
    tool_name: str,
    *,
    search_root: Path | None = None,
) -> ToolSnapshot:
    """Probe a single tool's availability, version, and capabilities.

    Args:
        tool_name: Registered tool name (case-insensitive).
        search_root: Project root for native-config discovery; defaults to cwd.

    Returns:
        Fresh ToolSnapshot (not read from cache).
    """
    from lintro.tools import tool_manager
    from lintro.tools.core.version_checking import get_install_hints
    from lintro.tools.core.version_parsing import check_tool_version

    root = search_root if search_root is not None else Path.cwd()
    name = tool_name.lower()

    try:
        plugin = tool_manager.get_tool(name)
        definition = plugin.definition
    except (KeyError, ValueError) as exc:
        manifest = _manifest_tool(name)
        if manifest is None or not manifest.version_command:
            return _unavailable_snapshot(
                name,
                probe_error=f"Tool not registered: {exc}",
            )
        command = list(manifest.version_command)
        binary_path = _resolved_binary(command)
        snapshot = ToolSnapshot(
            name=name,
            binary_path=binary_path,
            binary_mtime=_binary_mtime(binary_path),
            command=tuple(command),
        )
        version_info = check_tool_version(
            name,
            command,
            append_version=False,
            cwd=root,
        )
        return _snapshot_from_version(
            snapshot,
            version_info,
            get_install_hints().get(name, f"Install {name} and ensure it is on PATH"),
        )

    can_fix = bool(definition.can_fix)
    supports_stdin = definition.supports_stdin
    if not isinstance(supports_stdin, bool):
        supports_stdin = None
    config_found = _find_native_config(
        list(definition.native_configs or []),
        search_root=root,
    )
    capabilities = ToolCapabilities(
        can_fix=can_fix,
        supports_stdin=supports_stdin,
        config_found=config_found,
    )

    install_hints = get_install_hints()
    remediation = install_hints.get(name) or install_hints.get(
        name.replace("-", "_"),
        f"Install {name} and ensure it is on PATH",
    )

    # In-process tools (e.g. idiom-review) have no external binary and no
    # version gate — treat them as available without a PATH probe.
    if definition.version_command is None and definition.min_version is None:
        return ToolSnapshot(
            name=name,
            available=True,
            version=None,
            capabilities=capabilities,
            probe_error=None,
            remediation_hint=None,
            binary_path="",
            binary_mtime=0.0,
            version_check_passed=True,
            min_version="",
            recommended_version="",
            below_recommended=False,
        )

    command = get_executable_command(definition.name, cwd=root)
    binary_path = _resolved_binary(command)
    binary_mtime = _binary_mtime(binary_path)

    # Always run the version probe — do not gate on shutil.which alone.
    # Wrappers (bunx/npx/python -m) and test mocks of subprocess.run can
    # succeed even when the bare binary name is absent from PATH.
    version_info = check_tool_version(
        definition.name,
        command,
        append_version=True,
        cwd=root,
    )
    snapshot = ToolSnapshot(
        name=name,
        capabilities=capabilities,
        binary_path=binary_path,
        binary_mtime=binary_mtime,
        command=tuple(command),
    )
    return _snapshot_from_version(snapshot, version_info, remediation)


def _snapshot_from_version(
    snapshot: ToolSnapshot,
    version_info: ToolVersionInfo,
    remediation: str,
) -> ToolSnapshot:
    """Combine one version probe with its resolution and capability evidence.

    Args:
        snapshot: Command, path and declared capability evidence.
        version_info: Result of the shared version parser.
        remediation: Installation or upgrade guidance.

    Returns:
        Available, below-minimum or degraded snapshot without re-probing.
    """
    error = version_info.error_message or ""
    unparseable = bool(snapshot.binary_path and "Could not parse version" in error)
    failed_probe = version_info.current_version is None and any(
        text in error.lower()
        for text in ("failed to run version check", "command failed", "not found")
    )
    passed = version_info.version_check_passed and not failed_probe
    below_minimum = (
        version_info.current_version is not None and "below minimum" in error
    )
    available = passed or unparseable or below_minimum
    if not available and not snapshot.binary_path:
        main_cmd = snapshot.command[0] if snapshot.command else snapshot.name
        error = f"{main_cmd} not found in PATH. {error}".strip()
    return replace(
        snapshot,
        available=available,
        version=version_info.current_version if available else None,
        probe_error=None if passed else error or "tool unavailable",
        remediation_hint=remediation,
        version_check_passed=passed or unparseable,
        min_version=version_info.min_version,
        recommended_version=version_info.recommended_version or "",
        below_recommended=version_info.below_recommended,
    )


def _load_disk_cache(
    cache_file: Path,
    *,
    ttl: int,
    search_root: Path,
) -> tuple[dict[str, ToolSnapshot], float] | None:
    """Load snapshots from disk when the cache file is valid.

    Args:
        cache_file: Path to the JSON cache.
        ttl: Maximum age in seconds.
        search_root: Directory used to resolve current commands.

    Returns:
        Snapshot map, or None when missing / corrupt / expired / version mismatch.
    """
    if not cache_file.exists():
        return None
    try:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.debug("Corrupt tool snapshot cache {}; re-probing: {}", cache_file, exc)
        with contextlib.suppress(OSError):
            cache_file.unlink(missing_ok=True)
        return None

    if not isinstance(raw, dict):
        return None
    if raw.get("lintro_version") != __version__:
        logger.debug("Snapshot cache lintro version mismatch; re-probing")
        return None
    probed_at = raw.get("probed_at")
    if not isinstance(probed_at, (int, float)):
        return None
    age = time.time() - float(probed_at)
    if not 0 <= age <= ttl:
        logger.debug("Snapshot cache TTL expired; re-probing")
        return None

    snapshots_raw = raw.get("snapshots")
    if not isinstance(snapshots_raw, dict):
        return None

    snapshots: dict[str, ToolSnapshot] = {}
    for name, entry in snapshots_raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            snap = ToolSnapshot.from_dict(entry)
        except (TypeError, ValueError, KeyError):
            continue
        if not _is_cached_snapshot_fresh(snap, search_root=search_root):
            logger.debug(
                "Snapshot for {} invalidated by path/mtime or PATH change",
                name,
            )
            continue
        snapshots[name.lower()] = snap

    return (snapshots, float(probed_at)) if snapshots else None


def _write_disk_cache(
    cache_file: Path,
    snapshots: dict[str, ToolSnapshot],
    *,
    ttl: int,
) -> None:
    """Persist snapshots to disk.

    Args:
        cache_file: Destination JSON path.
        snapshots: Snapshot map to write.
        ttl: TTL recorded in the cache metadata.
    """
    payload = {
        "lintro_version": __version__,
        "probed_at": time.time(),
        "ttl_seconds": ttl,
        "snapshots": {name: snap.to_dict() for name, snap in snapshots.items()},
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_file.parent,
            delete=False,
            prefix=f"{cache_file.stem}.",
            suffix=".tmp",
        ) as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True))
            tmp_path = Path(handle.name)
        tmp_path.replace(cache_file)
    except OSError as exc:
        logger.debug("Failed to write tool snapshot cache {}: {}", cache_file, exc)


def _probe_all_fresh(
    tool_names: list[str],
    *,
    search_root: Path,
    max_workers: int,
) -> dict[str, ToolSnapshot]:
    """Probe tools in parallel and return the snapshot map.

    Args:
        tool_names: Tool names to probe.
        search_root: Project root for config discovery.
        max_workers: Thread pool size.

    Returns:
        Mapping of tool name → ToolSnapshot.
    """
    results: dict[str, ToolSnapshot] = {}
    if not tool_names:
        return results

    workers = max(1, min(max_workers, len(tool_names)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(probe_tool, name, search_root=search_root): name
            for name in tool_names
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name.lower()] = future.result()
            except Exception as exc:
                logger.debug("Probe failed for {}: {}", name, exc)
                results[name.lower()] = _unavailable_snapshot(
                    name.lower(),
                    probe_error=f"Probe failed: {exc}",
                )
    return results


def _memory_hits_for(
    names: list[str],
    *,
    cache_file: Path,
    ttl: int,
    search_root: Path,
) -> dict[str, ToolSnapshot] | None:
    """Return memory snapshots when they still cover ``names``.

    Must be called while holding ``_cache_lock``.
    """
    if (
        _memory_cache is None
        or _memory_cache_path != cache_file
        or _memory_probed_at is None
        or time.time() - _memory_probed_at > ttl
    ):
        return None
    hits: dict[str, ToolSnapshot] = {}
    for name in names:
        snap = lookup_snapshot(snapshots=_memory_cache, name=name)
        if snap is None or not _is_cached_snapshot_fresh(
            snap,
            search_root=search_root,
        ):
            return None
        hits[name] = snap
    return hits


def _configured_max_workers() -> int:
    """Return the configured probe parallelism.

    Returns:
        ``execution.max_workers`` from config, or 8 when config is unavailable.
    """
    try:
        from lintro.config.config_loader import get_config

        return get_config().execution.max_workers
    except (ImportError, OSError, ValueError, AttributeError, RuntimeError):
        return 8


def probe_all_tools(
    *,
    force: bool = False,
    cache_root: Path | None = None,
    tool_names: list[str] | None = None,
) -> dict[str, ToolSnapshot]:
    """Probe all registered tools (or a subset), using the snapshot cache.

    Args:
        force: Bypass cache and re-probe.
        cache_root: Directory for ``.lintro-cache``; defaults to cwd.
        tool_names: Optional subset of tool names; defaults to all registered.

    Returns:
        Mapping of tool name → ToolSnapshot.
    """
    global _memory_cache, _memory_cache_path, _memory_probed_at, _force_fresh

    from lintro.tools import tool_manager

    names = tool_names if tool_names is not None else tool_manager.get_tool_names()
    names = [n.lower() for n in names]
    search_root = (cache_root if cache_root is not None else Path.cwd()).resolve()
    cache_file = _cache_file_path(cache_root=cache_root)
    ttl = get_snapshot_ttl()

    max_workers = _configured_max_workers()

    force_effective = False
    with _cache_lock:
        force_effective = force or _force_fresh
        if _force_fresh:
            # Consume the flag at cycle start so a later set during the
            # probe is not wiped when this cycle used force=True from the
            # caller.
            _force_fresh = False
        if not force_effective:
            hits = _memory_hits_for(
                names,
                cache_file=cache_file,
                ttl=ttl,
                search_root=search_root,
            )
            if hits is not None:
                return hits

    # Disk I/O outside the lock so slow mounts do not block other callers.
    disk: tuple[dict[str, ToolSnapshot], float] | None = None
    if not force_effective:
        disk = _load_disk_cache(cache_file, ttl=ttl, search_root=search_root)

    with _cache_lock:
        # Re-check memory: another thread may have filled the cache.
        if not force_effective:
            hits = _memory_hits_for(
                names,
                cache_file=cache_file,
                ttl=ttl,
                search_root=search_root,
            )
            if hits is not None:
                return hits
        if disk is not None:
            disk_snapshots, disk_probed_at = disk
            disk_hits: dict[str, ToolSnapshot] = {}
            disk_complete = True
            for name in names:
                snap = lookup_snapshot(snapshots=disk_snapshots, name=name)
                if snap is None:
                    disk_complete = False
                    break
                disk_hits[name] = snap
            if disk_complete:
                _memory_cache = disk_snapshots
                _memory_cache_path = cache_file
                _memory_probed_at = disk_probed_at
                return disk_hits

    fresh = _probe_all_fresh(
        names,
        search_root=search_root,
        max_workers=max_workers,
    )

    with _cache_lock:
        # A subset refresh must not renew the age of retained older snapshots.
        _memory_cache = dict(fresh)
        _memory_cache_path = cache_file
        _memory_probed_at = time.time()
        to_write = dict(fresh)

    _write_disk_cache(cache_file, to_write, ttl=ttl)
    return {n: fresh[n] for n in names if n in fresh}


def get_tool_snapshot(
    tool_name: str,
    *,
    force: bool = False,
    cache_root: Path | None = None,
) -> ToolSnapshot:
    """Return a cached or freshly probed snapshot for one tool.

    Args:
        tool_name: Tool name (case-insensitive).
        force: Bypass cache for this tool (re-probes all when cache cold).
        cache_root: Optional cache root directory.

    Returns:
        ToolSnapshot for the requested tool.
    """
    name = tool_name.lower()
    snapshots = probe_all_tools(
        force=force,
        cache_root=cache_root,
        tool_names=[name],
    )
    if name in snapshots:
        return snapshots[name]
    return _unavailable_snapshot(name, probe_error="Probe returned no snapshot")


def snapshot_to_unavailable_result(
    snapshot: ToolSnapshot,
    *,
    strict: bool | None = None,
) -> ToolResult:
    """Build a ToolResult representing an unavailable tool.

    Args:
        snapshot: Unavailable (or version-failed) tool snapshot.
        strict: Override for ``strict_missing_tools``; None reads config.

    Returns:
        ToolResult with ``unavailable=True`` and remediation context.
    """
    from lintro.models.core.tool_outcome import ToolOutcome, Unavailable
    from lintro.models.core.tool_result import ToolResult

    strict_missing = is_strict_missing_tools() if strict is None else strict
    error = snapshot.probe_error or "tool unavailable"
    hint = snapshot.remediation_hint or ""
    message = f"{snapshot.name} unavailable: {error}"
    if hint:
        message = f"{message}. {hint}"

    result = ToolResult.from_outcome(
        ToolOutcome(execution=Unavailable(reason=error, strict=strict_missing)),
        name=snapshot.name,
        output=message,
    )
    return result


# The module itself satisfies the provider protocol; dynamic lookup preserves
# one cache authority, including scoped test replacements of probe functions.
register_snapshot_provider(cast(SnapshotProvider, sys.modules[__name__]))
