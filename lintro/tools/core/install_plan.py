"""Data classes for tool installation planning and results."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.tools.core.manifest_models import ManifestTool


@dataclass
class InstallResult:
    """Result of installing a single tool.

    Attributes:
        tool: The manifest tool entry.
        success: Whether installation succeeded.
        message: Human-readable result message.
        duration_seconds: How long the install took.
    """

    tool: ManifestTool
    success: bool
    message: str
    duration_seconds: float = 0.0


@dataclass
class InstallPlan:
    """Planned installation actions.

    Attributes:
        to_install: Tools to install with their install command.
        to_upgrade: Tools to upgrade (tool, current version, upgrade command).
        already_ok: Tools already installed at correct version.
        outdated: Tools with outdated versions (not upgrading).
        skipped: Tools skipped (e.g., Rust tools when cargo unavailable).
        manual: Tools that require manual installation, with install hints.
    """

    to_install: list[tuple[ManifestTool, str]] = field(default_factory=list)
    to_upgrade: list[tuple[ManifestTool, str, str]] = field(default_factory=list)
    already_ok: list[ManifestTool] = field(default_factory=list)
    outdated: list[tuple[ManifestTool, str]] = field(default_factory=list)
    skipped: list[tuple[ManifestTool, str]] = field(default_factory=list)
    manual: list[tuple[ManifestTool, str]] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        """Whether there are tools to install or upgrade."""
        return bool(self.to_install or self.to_upgrade)

    @property
    def total_actions(self) -> int:
        """Total number of install/upgrade actions."""
        return len(self.to_install) + len(self.to_upgrade)
