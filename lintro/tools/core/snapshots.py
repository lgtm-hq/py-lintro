"""Cached capability probing for registered Lintro tools.

Probes each tool once (availability, version, capabilities), caches the
result under ``.lintro-cache/tool-snapshots.json`` keyed on binary path,
mtime, and lintro version, and exposes snapshots to check/format runners,
``list-tools``, and ``doctor``.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from lintro import __version__ as LINTRO_VERSION

# Default TTL for on-disk snapshot cache (seconds).
DEFAULT_SNAPSHOT_TTL_SECONDS: int = 600

# Relative cache path under the project / cwd root.
SNAPSHOT_CACHE_RELPATH: str = ".lintro-cache/tool-snapshots.json"

_cache_lock = threading.Lock()
_memory_cache: dict[str, ToolSnapshot] | None = None
_memory_cache_path: Path | None = None
_memory_probed_at: float | None = None
_force_fresh: bool = False


@dataclass(frozen=True)
class ToolCapabilities:
    """Declared / discovered capabilities for a tool.

    Attributes:
        can_fix: Whether the tool can auto-fix issues.
        supports_stdin: Whether the tool accepts stdin input.
        config_found: Whether a native config file was found in the project.
    """

    can_fix: bool = False
    supports_stdin: bool = False
    config_found: bool = False

    def to_dict(self) -> dict[str, bool]:
        """Serialize capabilities for JSON cache / API output.

        Returns:
            Dictionary of capability flags.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ToolCapabilities:
        """Deserialize capabilities from a cache entry.

        Args:
            data: Raw capabilities dict, or None for defaults.

        Returns:
            ToolCapabilities instance.
        """
        if not data:
            return cls()
        return cls(
            can_fix=bool(data.get("can_fix", False)),
            supports_stdin=bool(data.get("supports_stdin", False)),
            config_found=bool(data.get("config_found", False)),
        )


@dataclass(frozen=True)
class ToolSnapshot:
    """Cached probe result for a single tool.

    Attributes:
        name: Canonical tool name.
        available: True when the binary exists and version probe succeeded.
        version: Detected version string, or None.
        capabilities: Capability flags for the tool.
        probe_error: Error message when unavailable or probe failed.
        remediation_hint: Install / upgrade hint for the user.
        binary_path: Resolved executable path (empty when missing).
        binary_mtime: mtime of the binary when probed (0.0 when missing).
        version_check_passed: Whether the installed version meets the minimum.
        min_version: Minimum required version used during the probe.
        recommended_version: Recommended version from requirements, if any.
        below_recommended: True when installed version is below recommended.
    """

    name: str
    available: bool = False
    version: str | None = None
    capabilities: ToolCapabilities = field(default_factory=ToolCapabilities)
    probe_error: str | None = None
    remediation_hint: str | None = None
    binary_path: str = ""
    binary_mtime: float = 0.0
    version_check_passed: bool = False
    min_version: str = ""
    recommended_version: str = ""
    below_recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the snapshot for JSON cache / API output.

        Returns:
            Dictionary representation of the snapshot.
        """
        return {
            "name": self.name,
            "available": self.available,
            "version": self.version,
            "capabilities": self.capabilities.to_dict(),
            "probe_error": self.probe_error,
            "remediation_hint": self.remediation_hint,
            "binary_path": self.binary_path,
            "binary_mtime": self.binary_mtime,
            "version_check_passed": self.version_check_passed,
            "min_version": self.min_version,
            "recommended_version": self.recommended_version,
            "below_recommended": self.below_recommended,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolSnapshot:
        """Deserialize a snapshot from a cache entry.

        Args:
            data: Raw snapshot dict.

        Returns:
            ToolSnapshot instance.
        """
        return cls(
            name=str(data.get("name", "")),
            available=bool(data.get("available", False)),
            version=data.get("version"),
            capabilities=ToolCapabilities.from_dict(data.get("capabilities")),
            probe_error=data.get("probe_error"),
            remediation_hint=data.get("remediation_hint"),
            binary_path=str(data.get("binary_path", "") or ""),
            binary_mtime=float(data.get("binary_mtime", 0.0) or 0.0),
            version_check_passed=bool(data.get("version_check_passed", False)),
            min_version=str(data.get("min_version", "") or ""),
            recommended_version=str(data.get("recommended_version", "") or ""),
            below_recommended=bool(data.get("below_recommended", False)),
        )

    def cache_key_matches(
        self,
        *,
        binary_path: str,
        binary_mtime: float,
    ) -> bool:
        """Return True when path+mtime still match this snapshot.

        Args:
            binary_path: Current resolved binary path.
            binary_mtime: Current binary mtime (0.0 when missing).

        Returns:
            True if the cache key components still match.
        """
        return self.binary_path == binary_path and self.binary_mtime == binary_mtime


def set_force_fresh_probes(force: bool) -> None:
    """Force the next probe cycle to ignore on-disk and memory caches.

    Args:
        force: When True, probes re-run even if a valid cache exists.
    """
    global _force_fresh
    with _cache_lock:
        _force_fresh = force


def clear_snapshot_cache(*, cache_root: Path | None = None) -> None:
    """Clear in-memory and on-disk tool snapshot caches.

    Args:
        cache_root: Directory containing ``.lintro-cache``; defaults to cwd.
    """
    global _memory_cache, _memory_cache_path, _memory_probed_at
    with _cache_lock:
        _memory_cache = None
        _memory_cache_path = None
        _memory_probed_at = None
        cache_file = _cache_file_path(cache_root=cache_root)
        if cache_file.exists():
            try:
                cache_file.unlink()
                logger.debug("Deleted tool snapshot cache: {}", cache_file)
            except OSError as exc:
                logger.warning("Could not delete snapshot cache {}: {}", cache_file, exc)


def get_snapshot_ttl() -> int:
    """Return the configured snapshot TTL in seconds.

    Returns:
        TTL from execution config, or the default when config is unavailable.
    """
    try:
        from lintro.config.config_loader import get_config

        ttl = get_config().execution.tool_snapshot_ttl
        if isinstance(ttl, int) and ttl >= 1:
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
    from lintro.plugins.execution_preparation import get_executable_command
    from lintro.plugins.registry import ToolRegistry
    from lintro.tools.core.version_checking import get_install_hints
    from lintro.tools.core.version_parsing import check_tool_version

    root = search_root if search_root is not None else Path.cwd()
    name = tool_name.lower()

    try:
        plugin = ToolRegistry.get(name)
        definition = plugin.definition
    except (KeyError, ValueError) as exc:
        return _unavailable_snapshot(
            name,
            probe_error=f"Tool not registered: {exc}",
        )

    can_fix = bool(definition.can_fix)
    # Stdin support is not yet declared on ToolDefinition; leave False for v1
    # so the capability surface is present without inventing false positives.
    supports_stdin = False
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

    command = get_executable_command(definition.name)
    main_cmd = command[0] if command else definition.name
    binary_path = shutil.which(main_cmd) or ""
    binary_mtime = _binary_mtime(binary_path)

    # Always run the version probe — do not gate on shutil.which alone.
    # Wrappers (bunx/npx/python -m) and test mocks of subprocess.run can
    # succeed even when the bare binary name is absent from PATH.
    version_info = check_tool_version(
        definition.name,
        command,
        append_version=True,
    )
    recommended = version_info.recommended_version or ""
    below_rec = bool(version_info.below_recommended)

    if (
        version_info.current_version is None
        and version_info.error_message
        and "Could not parse version" in version_info.error_message
        and binary_path
    ):
        # Unparseable version with a live binary: treat as available but
        # note that the version check could not be completed.
        return ToolSnapshot(
            name=name,
            available=True,
            version=None,
            capabilities=capabilities,
            probe_error=version_info.error_message,
            remediation_hint=remediation,
            binary_path=binary_path,
            binary_mtime=binary_mtime,
            version_check_passed=True,
            min_version=version_info.min_version,
            recommended_version=recommended,
            below_recommended=below_rec,
        )

    if version_info.version_check_passed:
        # Tools without a declared minimum still set version_check_passed on
        # OSError. Treat a failed probe with no parsed version as unavailable
        # so missing binaries degrade visibly instead of proceeding.
        if (
            version_info.current_version is None
            and version_info.error_message
            and (
                "Failed to run version check" in version_info.error_message
                or "Command failed" in version_info.error_message
                or "not found" in version_info.error_message.lower()
            )
        ):
            probe_error = (
                f"{main_cmd} not found in PATH"
                if not binary_path
                else version_info.error_message
            )
            return _unavailable_snapshot(
                name,
                probe_error=probe_error,
                remediation_hint=remediation,
                binary_path=binary_path,
                binary_mtime=binary_mtime,
                capabilities=capabilities,
                min_version=version_info.min_version,
            )
        return ToolSnapshot(
            name=name,
            available=True,
            version=version_info.current_version,
            capabilities=capabilities,
            probe_error=None,
            remediation_hint=remediation,
            binary_path=binary_path,
            binary_mtime=binary_mtime,
            version_check_passed=True,
            min_version=version_info.min_version,
            recommended_version=recommended,
            below_recommended=below_rec,
        )

    # Version below minimum: still "available" so consumers can skip with
    # the same remediation messaging as before.
    if (
        version_info.current_version is not None
        and version_info.error_message
        and "below minimum" in version_info.error_message
    ):
        return ToolSnapshot(
            name=name,
            available=True,
            version=version_info.current_version,
            capabilities=capabilities,
            probe_error=version_info.error_message,
            remediation_hint=remediation,
            binary_path=binary_path,
            binary_mtime=binary_mtime,
            version_check_passed=False,
            min_version=version_info.min_version,
            recommended_version=recommended,
            below_recommended=below_rec,
        )

    probe_error = version_info.error_message or f"{main_cmd} not found in PATH"
    if not binary_path and not version_info.error_message:
        probe_error = f"{main_cmd} not found in PATH"

    return _unavailable_snapshot(
        name,
        probe_error=probe_error,
        remediation_hint=remediation,
        binary_path=binary_path,
        binary_mtime=binary_mtime,
        capabilities=capabilities,
        min_version=version_info.min_version,
    )


def _load_disk_cache(
    cache_file: Path,
    *,
    ttl: int,
) -> dict[str, ToolSnapshot] | None:
    """Load snapshots from disk when the cache file is valid.

    Args:
        cache_file: Path to the JSON cache.
        ttl: Maximum age in seconds.

    Returns:
        Snapshot map, or None when missing / corrupt / expired / version mismatch.
    """
    if not cache_file.exists():
        return None
    try:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.debug("Corrupt tool snapshot cache {}; re-probing: {}", cache_file, exc)
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    if not isinstance(raw, dict):
        return None
    if raw.get("lintro_version") != LINTRO_VERSION:
        logger.debug("Snapshot cache lintro version mismatch; re-probing")
        return None
    probed_at = raw.get("probed_at")
    if not isinstance(probed_at, (int, float)):
        return None
    if time.time() - float(probed_at) > ttl:
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
        # Invalidate entries whose binary path/mtime drifted (tool upgrade).
        if snap.binary_path:
            if not Path(snap.binary_path).exists():
                logger.debug(
                    "Snapshot for {} invalidated; binary missing at {}",
                    name,
                    snap.binary_path,
                )
                continue
            current_path = snap.binary_path
            current_mtime = _binary_mtime(current_path)
        else:
            current_path = ""
            current_mtime = 0.0
        if not snap.cache_key_matches(
            binary_path=current_path,
            binary_mtime=current_mtime,
        ):
            logger.debug(
                "Snapshot for {} invalidated by binary path/mtime change",
                name,
            )
            continue
        snapshots[name.lower()] = snap

    return snapshots if snapshots else None


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
        "lintro_version": LINTRO_VERSION,
        "probed_at": time.time(),
        "ttl_seconds": ttl,
        "snapshots": {name: snap.to_dict() for name, snap in snapshots.items()},
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(cache_file)
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
            except Exception as exc:  # noqa: BLE001 - keep probing other tools
                logger.debug("Probe failed for {}: {}", name, exc)
                results[name.lower()] = _unavailable_snapshot(
                    name.lower(),
                    probe_error=f"Probe failed: {exc}",
                )
    return results


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

    from lintro.plugins.discovery import discover_all_tools
    from lintro.plugins.registry import ToolRegistry

    discover_all_tools()
    names = tool_names if tool_names is not None else ToolRegistry.get_names()
    names = [n.lower() for n in names]
    root = cache_root if cache_root is not None else Path.cwd()
    cache_file = _cache_file_path(cache_root=root)
    ttl = get_snapshot_ttl()

    try:
        from lintro.config.config_loader import get_config

        max_workers = get_config().execution.max_workers
    except (ImportError, OSError, ValueError, AttributeError, RuntimeError):
        max_workers = 8

    force_effective = False
    with _cache_lock:
        force_effective = force or _force_fresh
        if (
            not force_effective
            and _memory_cache is not None
            and _memory_cache_path == cache_file
            and _memory_probed_at is not None
            and time.time() - _memory_probed_at <= ttl
            and all(n in _memory_cache for n in names)
        ):
            return {n: _memory_cache[n] for n in names}

    # Disk I/O outside the lock so slow mounts do not block other callers.
    disk: dict[str, ToolSnapshot] | None = None
    if not force_effective:
        disk = _load_disk_cache(cache_file, ttl=ttl)

    with _cache_lock:
        # Re-check memory: another thread may have filled the cache.
        if (
            not force_effective
            and _memory_cache is not None
            and _memory_cache_path == cache_file
            and _memory_probed_at is not None
            and time.time() - _memory_probed_at <= ttl
            and all(n in _memory_cache for n in names)
        ):
            return {n: _memory_cache[n] for n in names}
        if disk is not None and all(n in disk for n in names):
            _memory_cache = disk
            _memory_cache_path = cache_file
            _memory_probed_at = time.time()
            return {n: disk[n] for n in names}

    fresh = _probe_all_fresh(names, search_root=root, max_workers=max_workers)

    with _cache_lock:
        merged = dict(_memory_cache or {})
        merged.update(fresh)
        _memory_cache = merged
        _memory_cache_path = cache_file
        _memory_probed_at = time.time()
        _force_fresh = False
        to_write = dict(merged)

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
) -> "ToolResult":
    """Build a ToolResult representing an unavailable tool.

    Args:
        snapshot: Unavailable (or version-failed) tool snapshot.
        strict: Override for ``strict_missing_tools``; None reads config.

    Returns:
        ToolResult with ``unavailable=True`` and remediation context.
    """
    from lintro.models.core.tool_result import ToolResult

    strict_missing = is_strict_missing_tools() if strict is None else strict
    error = snapshot.probe_error or "tool unavailable"
    hint = snapshot.remediation_hint or ""
    message = f"{snapshot.name} unavailable: {error}"
    if hint:
        message = f"{message}. {hint}"

    return ToolResult(
        name=snapshot.name,
        success=not strict_missing,
        output=message,
        issues_count=0,
        unavailable=True,
        skip_reason=error,
    )
