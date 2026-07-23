"""Unified manifest registry loaded from manifest.json.

This module owns static tool *metadata* (versions, install commands,
language mappings, profiles) parsed from ``manifest.json``. It is the single
source of truth for that metadata and replaces three parallel
tool-to-version-command dicts that previously existed in:
- doctor.py (TOOL_COMMANDS)
- version_requirements.py (tool_commands)
- runtime_discovery.py (TOOL_VERSION_COMMANDS)

This is distinct from :class:`lintro.plugins.registry.ToolRegistry`, which
tracks *live plugin instances* (registered ``BaseToolPlugin`` subclasses)
rather than manifest metadata.

Usage:
    from lintro.tools.core.tool_registry import ManifestRegistry

    registry = ManifestRegistry.load()
    tool = registry.get("ruff")
    print(tool.version_command)  # ["ruff", "--version"]
"""

from __future__ import annotations

import json
import logging
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any

from lintro.tools.core.manifest_models import ManifestTool, ProfileDefinition

# Re-export so existing ``from lintro.tools.core.tool_registry import ManifestTool``
# continues to work.
__all__ = [
    "CATEGORY_LABELS",
    "ManifestRegistry",
    "ManifestTool",
    "ProfileDefinition",
    "ToolRegistry",  # noqa: F822 - deprecated alias resolved via module __getattr__
]

# Use stdlib logging to avoid external dependencies during early imports
_logger = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"

# Display labels for tool categories
CATEGORY_LABELS: dict[str, str] = {
    "bundled": "Bundled Python tools",
    "npm": "npm tools",
    "external": "External tools",
}


class ManifestRegistry:
    """Single source of truth for all tool metadata.

    Loaded from manifest.json v2. Provides typed access to tool metadata,
    version commands, language mappings, and profiles.

    Not to be confused with :class:`lintro.plugins.registry.ToolRegistry`,
    which manages live plugin instances rather than manifest metadata.
    """

    def __init__(
        self,
        tools: dict[str, ManifestTool],
        language_map: dict[str, list[str]],
        profiles: dict[str, ProfileDefinition],
    ) -> None:
        """Initialize the registry with parsed manifest data."""
        self._tools = tools
        self._language_map = language_map
        self._profiles = profiles

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> ManifestRegistry:
        """Load the registry from manifest.json.

        Cached via lru_cache — call clear_cache() to force a reload.

        Returns:
            ManifestRegistry: Loaded and cached registry instance.
        """
        return cls._load_from_path(_MANIFEST_PATH)

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the cached registry so the next load() re-reads manifest.json.

        Useful for tests and development workflows where the manifest may
        change between calls.
        """
        cls.load.cache_clear()

    @classmethod
    def _load_from_path(cls, path: Path) -> ManifestRegistry:
        """Load registry from a specific manifest path.

        Args:
            path: Path to manifest.json.

        Returns:
            ManifestRegistry: Loaded registry.

        Raises:
            ValueError: If the manifest is malformed or has an invalid version.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(
                f"manifest root must be a JSON object, got {type(data).__name__}",
            )
        raw_version = data.get("version", 1)
        try:
            manifest_version = int(raw_version)
        except (TypeError, ValueError):
            raise ValueError(
                f"manifest 'version' must be an integer, got {raw_version!r}",
            ) from None

        # Validate and parse tools
        raw_tools = data.get("tools", [])
        if not isinstance(raw_tools, list):
            raise ValueError(
                f"manifest 'tools' must be a list, got {type(raw_tools).__name__}",
            )
        tools: dict[str, ManifestTool] = {}
        for entry in raw_tools:
            tool = cls._parse_tool_entry(entry, manifest_version)
            if tool:
                tools[tool.name] = tool

        # Validate and parse language_map (v2 only)
        language_map: dict[str, list[str]] = data.get("language_map", {})
        if not isinstance(language_map, dict):
            kind = type(language_map).__name__
            raise ValueError(
                f"manifest 'language_map' must be a dict, got {kind}",
            )

        # Validate and parse profiles (v2 only)
        raw_profiles = data.get("profiles", {})
        if not isinstance(raw_profiles, dict):
            kind = type(raw_profiles).__name__
            raise ValueError(
                f"manifest 'profiles' must be a dict, got {kind}",
            )
        profiles: dict[str, ProfileDefinition] = {}
        for name, pdata in raw_profiles.items():
            profiles[name] = ProfileDefinition(
                name=name,
                description=pdata.get("description", ""),
                strategy=pdata.get("strategy", "explicit"),
                tools=tuple(pdata.get("tools", [])),
                exclude_types=tuple(pdata.get("exclude_types", [])),
            )

        _logger.debug(
            "Loaded %d tools, %d profiles from manifest v%d",
            len(tools),
            len(profiles),
            manifest_version,
        )
        return cls(tools=tools, language_map=language_map, profiles=profiles)

    @staticmethod
    def _parse_tool_entry(
        entry: dict[str, Any],
        manifest_version: int,
    ) -> ManifestTool | None:
        """Parse a single tool entry from manifest data.

        Args:
            entry: Raw dict from manifest JSON.
            manifest_version: Manifest schema version.

        Returns:
            ManifestTool or None if entry is invalid.
        """
        name = entry.get("name")
        version = entry.get("version")
        if not name or not version:
            return None

        install = entry.get("install", {})
        install_type = install.get("type", "binary")

        # v2: version_command at top level; v1 compat: fall back to install
        if manifest_version >= 2:
            version_command = entry.get("version_command", [])
            if not isinstance(version_command, list) or not all(
                isinstance(t, str) and t.strip() for t in version_command
            ):
                _logger.warning(
                    "Tool %r has invalid version_command %r, treating as absent",
                    name,
                    version_command,
                )
                version_command = []
        else:
            version_command = entry.get("version_command") or install.get(
                "version_command",
                [],
            )

        # v1 compat: derive category from install type if not present
        category = entry.get("category")
        if not category:
            category_map = {
                "pip": "bundled",
                "npm": "npm",
                "binary": "external",
                "cargo": "external",
                "rustup": "external",
            }
            category = category_map.get(install_type, "external")

        raw_min = entry.get("min_version")
        min_version = str(raw_min) if raw_min else str(version)

        # Validate min_version <= version
        try:
            from lintro.tools.core.version_parsing import compare_versions

            if compare_versions(min_version, str(version)) > 0:
                _logger.warning(
                    "Tool %r has min_version %r > version %r; "
                    "clamping min_version to version",
                    name,
                    min_version,
                    version,
                )
                min_version = str(version)
        except (ValueError, ImportError):
            pass

        return ManifestTool(
            name=name,
            version=str(version),
            min_version=min_version,
            install_type=install_type,
            install_package=install.get("package"),
            install_bin=install.get("bin"),
            install_component=install.get("component"),
            tier=entry.get("tier", "tools"),
            category=category,
            version_command=tuple(version_command),
            languages=tuple(entry.get("languages", [])),
            tags=tuple(entry.get("tags", [])),
        )

    # ── Query methods ──────────────────────────────────────────────

    def get(self, name: str) -> ManifestTool:
        """Get a tool by name.

        Args:
            name: Tool name (e.g., "ruff").

        Returns:
            ManifestTool.

        Raises:
            KeyError: If tool is not in the registry.
        """
        if name not in self._tools:
            raise KeyError(
                f"Tool {name!r} not in registry. "
                f"Known tools: {sorted(self._tools)}",
            )
        return self._tools[name]

    def get_or_none(self, name: str) -> ManifestTool | None:
        """Get a tool by name, returning None if not found.

        Args:
            name: Tool name.

        Returns:
            ManifestTool or None.
        """
        return self._tools.get(name)

    def all_tools(self, *, include_dev: bool = False) -> list[ManifestTool]:
        """Get all tools, optionally including dev-tier tools.

        Args:
            include_dev: If True, include tools with tier="dev".

        Returns:
            List of ManifestTool sorted by name.
        """
        all_vals = list(self._tools.values())
        if not include_dev:
            all_vals = [t for t in all_vals if t.tier != "dev"]
        return sorted(all_vals, key=lambda t: t.name)

    def tools_for_languages(self, langs: list[str]) -> list[ManifestTool]:
        """Get tools recommended for the given languages/ecosystems.

        Uses the language_map to resolve language names to tool lists,
        then returns the union of all matching tools.

        Args:
            langs: List of language/ecosystem names (e.g., ["python", "docker"]).

        Returns:
            Deduplicated, sorted list of ManifestTool.
        """
        tool_names: set[str] = set()
        for lang in langs:
            lang_lower = lang.lower()
            if lang_lower in self._language_map:
                tool_names.update(self._language_map[lang_lower])

        # Always include security tools; yaml/markdown/toml only when detected
        if "security" in self._language_map:
            tool_names.update(self._language_map["security"])

        return sorted(
            [self._tools[n] for n in tool_names if n in self._tools],
            key=lambda t: t.name,
        )

    def tools_for_profile(
        self,
        profile_name: str,
        detected_langs: list[str] | None = None,
    ) -> list[ManifestTool]:
        """Resolve a profile to a concrete tool list.

        Args:
            profile_name: Profile name (e.g., "minimal", "recommended").
            detected_langs: Detected languages (for "auto-detect" strategy).

        Returns:
            List of ManifestTool for the profile.

        Raises:
            KeyError: If profile is not defined.
        """
        if profile_name not in self._profiles:
            raise KeyError(
                f"Profile {profile_name!r} not found. "
                f"Available: {sorted(self._profiles)}",
            )

        profile = self._profiles[profile_name]

        if profile.strategy == "explicit":
            return sorted(
                [self._tools[n] for n in profile.tools if n in self._tools],
                key=lambda t: t.name,
            )

        if profile.strategy == "auto-detect":
            if not detected_langs:
                # Fall back to minimal if no languages detected, guarding
                # against infinite recursion if minimal is also auto-detect.
                if (
                    "minimal" not in self._profiles
                    or self._profiles["minimal"].strategy == "auto-detect"
                ):
                    return []
                return self.tools_for_profile("minimal")
            return self.tools_for_languages(detected_langs)

        if profile.strategy == "all":
            return self.all_tools(include_dev=True)

        if profile.strategy == "filter":
            # Start with recommended tools, then exclude tools whose tags
            # are a subset of the exclude set (pure formatters are excluded,
            # but tools that are both linter+formatter are kept).
            exclude = set(profile.exclude_types)
            base = self.tools_for_profile("recommended", detected_langs)
            return [t for t in base if not t.tags or not set(t.tags).issubset(exclude)]

        _logger.warning(
            "Unknown profile strategy %r for profile %r",
            profile.strategy,
            profile.name,
        )
        return []

    def tools_by_category(self) -> dict[str, list[ManifestTool]]:
        """Group all tools by their display category.

        Returns:
            Dict mapping category name to list of tools.
            Keys are ordered: bundled, npm, external.
        """
        groups: dict[str, list[ManifestTool]] = {}
        for tool in sorted(self._tools.values(), key=lambda t: t.name):
            groups.setdefault(tool.category, []).append(tool)

        # Return in display order
        ordered: dict[str, list[ManifestTool]] = {}
        for cat in ("bundled", "npm", "external"):
            if cat in groups:
                ordered[cat] = groups[cat]
        # Add any remaining categories
        for cat, tools in groups.items():
            if cat not in ordered:
                ordered[cat] = tools
        return ordered

    def version_command(self, name: str) -> tuple[str, ...]:
        """Get the version check command for a tool.

        Args:
            name: Tool name.

        Returns:
            Command tuple (e.g., ("ruff", "--version")).
        """
        return self.get(name).version_command

    @property
    def profile_names(self) -> list[str]:
        """Get all available profile names."""
        return sorted(self._profiles)

    @property
    def profiles(self) -> dict[str, ProfileDefinition]:
        """Get all profile definitions."""
        return dict(self._profiles)

    @property
    def language_map(self) -> dict[str, list[str]]:
        """Get the language-to-tools mapping."""
        return dict(self._language_map)

    def __len__(self) -> int:
        """Return the number of tools in the registry."""
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """Check if a tool is in the registry."""
        return name in self._tools


def __getattr__(name: str) -> Any:
    """Provide a deprecated ``ToolRegistry`` alias for :class:`ManifestRegistry`.

    Implements `PEP 562 <https://peps.python.org/pep-0562/>`_ module-level
    attribute access so that ``from lintro.tools.core.tool_registry import
    ToolRegistry`` keeps working, while emitting a ``DeprecationWarning`` the
    first time the old name is used.

    Args:
        name: Attribute name being looked up on this module.

    Returns:
        ``ManifestRegistry`` when ``name`` is ``"ToolRegistry"``.

    Raises:
        AttributeError: If ``name`` is not a recognized module attribute.
    """
    if name == "ToolRegistry":
        warnings.warn(
            "lintro.tools.core.tool_registry.ToolRegistry is deprecated and "
            "will be removed in a future release; use ManifestRegistry "
            "instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return ManifestRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
