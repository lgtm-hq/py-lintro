"""Data classes for the tool manifest schema."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ManifestTool:
    """Single tool entry from manifest.json v2.

    Attributes:
        name: Canonical tool name (e.g., "ruff", "hadolint").
        version: Expected/minimum version string.
        install_type: Installation method (pip, npm, binary, cargo, rustup).
        install_package: Package name for pip/npm/cargo installs.
        install_bin: Binary name if different from package.
        install_component: Rustup component name (e.g., "clippy").
        tier: Tool tier — "tools" (production) or "dev" (optional).
        category: Display grouping — "bundled", "npm", or "external".
        version_command: Command to check installed version.
        languages: Language/ecosystem tags for project detection.
        tags: Semantic tags (e.g., "formatter", "linter", "security").
    """

    name: str
    version: str
    install_type: str
    install_package: str | None = None
    install_bin: str | None = None
    install_component: str | None = None
    tier: str = "tools"
    category: str = "external"
    version_command: tuple[str, ...] = field(default_factory=tuple)
    languages: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProfileDefinition:
    """Named tool profile from manifest.json.

    Attributes:
        name: Profile identifier (e.g., "minimal", "recommended").
        description: Human-readable description.
        strategy: Resolution strategy — "explicit", "auto-detect", "all", "filter".
        tools: Explicit tool list (for "explicit" strategy).
        exclude_types: Tool types to exclude (for "filter" strategy).
    """

    name: str
    description: str
    strategy: str = "explicit"
    tools: tuple[str, ...] = field(default_factory=tuple)
    exclude_types: tuple[str, ...] = field(default_factory=tuple)
