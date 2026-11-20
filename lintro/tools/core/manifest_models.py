"""Data classes for the tool manifest schema."""

from __future__ import annotations

from dataclasses import dataclass, field

from packaging.specifiers import InvalidSpecifier, SpecifierSet


@dataclass(frozen=True)
class KnownBadVersion:
    """A tool release lintro refuses because it gives wrong results.

    A bad release can sit between good ones, so a minimum alone cannot
    exclude it (#3556). The specifier is parsed on construction: an entry
    that cannot be read is an error, never an entry that matches nothing.

    Attributes:
        versions: PEP 440 version specifier naming the bad release or range,
            e.g. ``"==2.7.0"`` or ``">=2.4.0,<2.6.0"``.
        reason: What the release gets wrong, in plain words.
        reference: Where the defect is recorded (issue, PR or release URL).
        specifier: ``versions`` parsed.
    """

    versions: str
    reason: str
    reference: str
    specifier: SpecifierSet = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Parse the specifier and require a reason and a reference.

        Raises:
            ValueError: If a field is empty or ``versions`` is not a PEP 440
                specifier.
        """
        for name in ("versions", "reason", "reference"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"known-bad entry needs a non-empty {name!r}")
        try:
            specifier = SpecifierSet(self.versions)
        except InvalidSpecifier as exc:
            raise ValueError(
                f"known-bad versions {self.versions!r} is not a PEP 440 "
                "specifier such as '==2.7.0' or '>=2.4.0,<2.6.0'",
            ) from exc
        object.__setattr__(self, "specifier", specifier)


@dataclass(frozen=True)
class ManifestTool:
    """Single tool entry from manifest.json v2.

    Attributes:
        name: Canonical tool name (e.g., "ruff", "hadolint").
        version: Recommended/tested version string.
        min_version: Hard minimum compatible version (required; set equal to
            version when compatibility range is not yet proven).
        install_type: Installation method (pip, npm, binary, cargo, rustup).
        install_package: Package name for pip/npm/cargo installs.
        install_bin: Binary name if different from package.
        install_component: Rustup component name (e.g., "clippy").
        update_channel: Optional install-channel override when path heuristics
            fail (e.g., "homebrew", "uv_tool").
        tier: Tool tier — "tools" (production) or "dev" (optional).
        category: Display grouping — "bundled", "npm", or "external".
        version_command: Command to check installed version.
        languages: Language/ecosystem tags for project detection.
        tags: Semantic tags (e.g., "formatter", "linter", "security").
        known_bad_versions: Releases refused even when they meet
            ``min_version``.
    """

    name: str
    version: str
    min_version: str
    install_type: str
    install_package: str | None = None
    install_bin: str | None = None
    install_component: str | None = None
    update_channel: str | None = None
    tier: str = "tools"
    category: str = "external"
    version_command: tuple[str, ...] = field(default_factory=tuple)
    languages: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    known_bad_versions: tuple[KnownBadVersion, ...] = field(default_factory=tuple)


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
