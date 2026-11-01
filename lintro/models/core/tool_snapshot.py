"""Immutable tool capability evidence shared by execution and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
            command=tuple(data.get("command", ())),
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
