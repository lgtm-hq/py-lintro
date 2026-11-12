"""Immutable tool capability evidence shared by execution and reporting."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from lintro.enums.probe_failure import ProbeFailure


@dataclass(frozen=True)
class ToolCapabilities:
    """Declared / discovered capabilities for a tool.

    Attributes:
        can_fix: Whether the tool can auto-fix issues.
        supports_stdin: Whether the tool accepts stdin, or None when unspecified.
        config_found: Whether a native config file was found in the project.
    """

    can_fix: bool = False
    supports_stdin: bool | None = None
    config_found: bool = False

    def to_dict(self) -> dict[str, bool | None]:
        """Serialize capabilities for JSON cache / API output.

        Returns:
            Dictionary of capability flags.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> ToolCapabilities:
        """Deserialize capabilities from a cache entry.

        Args:
            data: Raw capabilities dict, or None for defaults.

        Returns:
            ToolCapabilities instance.

        Raises:
            ValueError: If the cache entry contains malformed capability flags.
        """
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ValueError("capabilities must be an object")
        can_fix = data.get("can_fix", False)
        supports_stdin = data.get("supports_stdin")
        config_found = data.get("config_found", False)
        if not isinstance(can_fix, bool) or not isinstance(config_found, bool):
            raise ValueError("capability flags must be booleans")
        if supports_stdin is not None and not isinstance(supports_stdin, bool):
            raise ValueError("stdin support must be boolean or null")
        return cls(
            can_fix=can_fix,
            supports_stdin=supports_stdin,
            config_found=config_found,
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
        command: Executable command resolved from the probe directory.
        probed_at: Original probe time, preserved across subset cache writes.
        probe_failure: Structured cause of a failed probe, when known.
        probe_seconds: Wall-clock seconds a timed-out probe spent, or None.
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
    command: tuple[str, ...] = ()
    probed_at: float = 0.0
    probe_failure: ProbeFailure | None = None
    probe_seconds: float | None = None

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
            "command": list(self.command),
            "probed_at": self.probed_at,
            "probe_failure": (
                None if self.probe_failure is None else str(self.probe_failure)
            ),
            "probe_seconds": self.probe_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolSnapshot:
        """Deserialize a snapshot from a cache entry.

        Args:
            data: Raw snapshot dict.

        Returns:
            ToolSnapshot instance.

        Raises:
            ValueError: If any cached field has an invalid type or value.
        """
        defaults = cls(name="").to_dict()
        values = {key: data.get(key, default) for key, default in defaults.items()}
        for key in ("available", "version_check_passed", "below_recommended"):
            if not isinstance(values[key], bool):
                raise ValueError(f"{key} must be a boolean")
        for key in ("version", "probe_error", "remediation_hint"):
            if values[key] is not None and not isinstance(values[key], str):
                raise ValueError(f"{key} must be a string or null")
        for key in ("name", "binary_path", "min_version", "recommended_version"):
            if not isinstance(values[key], str):
                raise ValueError(f"{key} must be a string")
        for key in ("binary_mtime", "probed_at"):
            value = values[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= sys.float_info.max
            ):
                raise ValueError(f"{key} must be a finite nonnegative number")
        probe_failure = values["probe_failure"]
        if probe_failure is not None and probe_failure not in set(ProbeFailure):
            raise ValueError("probe_failure must be a known probe failure or null")
        probe_seconds = values["probe_seconds"]
        if probe_seconds is not None and (
            isinstance(probe_seconds, bool)
            or not isinstance(probe_seconds, (int, float))
            or not 0 <= probe_seconds <= sys.float_info.max
        ):
            raise ValueError("probe_seconds must be a finite nonnegative number")
        # Probe timing is evidence of a timeout only; any other pairing would
        # load here and then fail when the result is built (#2337).
        if probe_seconds is not None and probe_failure != ProbeFailure.PROBE_TIMEOUT:
            raise ValueError("probe_seconds requires a probe_timeout failure")
        # Missing command evidence cannot establish a cache's binary identity.
        # Explicit in-process snapshots still serialize an empty command list.
        command = data.get("command")
        if not isinstance(command, (list, tuple)) or any(
            not isinstance(part, str) for part in command
        ):
            raise ValueError("command must be a list of strings")
        return cls(
            name=values["name"],
            available=values["available"],
            version=values["version"],
            capabilities=ToolCapabilities.from_dict(data.get("capabilities")),
            probe_error=values["probe_error"],
            remediation_hint=values["remediation_hint"],
            binary_path=values["binary_path"],
            binary_mtime=float(values["binary_mtime"]),
            version_check_passed=values["version_check_passed"],
            min_version=values["min_version"],
            recommended_version=values["recommended_version"],
            below_recommended=values["below_recommended"],
            command=tuple(command),
            probed_at=float(values["probed_at"]),
            probe_failure=(
                None if probe_failure is None else ProbeFailure(probe_failure)
            ),
            probe_seconds=None if probe_seconds is None else float(probe_seconds),
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
