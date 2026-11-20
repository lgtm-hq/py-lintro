"""Data classes for the tool manifest schema."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from packaging.specifiers import InvalidSpecifier, SpecifierSet

#: One clause of the known-bad ``versions`` grammar: ``==``, ``!=``, ``<``,
#: ``<=``, ``>`` or ``>=`` and a plain numeric release, with a trailing ``.*``
#: only after ``==`` and ``!=``. The manifest generator enforces the same
#: grammar (``lintro_build/versions/known_bad.py``), so a generated manifest
#: never holds an entry this rejects.
KNOWN_BAD_CLAUSE = re.compile(
    r" *(?:(?:==|!=) *[0-9]+(?:\.[0-9]+)*(?:\.\*)?"
    r"|(?:<=|>=|<|>) *[0-9]+(?:\.[0-9]+)*) *",
)

#: The grammar in words, for error messages.
KNOWN_BAD_GRAMMAR = (
    "comma-separated clauses, each ==, !=, <, <=, > or >= followed by a "
    "plain numeric release such as 2.7.0, with a trailing .* only after == "
    "or != (e.g. '==2.7.0', '>=2.4.0,<2.6.0', '==2.7.*'); no ~=, "
    "pre-release, post-release, epoch or local parts"
)


class ManifestError(ValueError):
    """``manifest.json`` holds an entry lintro cannot apply.

    A ``ValueError`` so existing handlers keep working; the CLI reports it as
    a one-line error naming the tool and entry instead of a traceback.
    """


def known_bad_versions_accepted(versions: str) -> bool:
    """Whether ``versions`` follows the known-bad grammar.

    Args:
        versions: A known-bad entry's ``versions`` value.

    Returns:
        True when every comma-separated clause matches the grammar.
    """
    return all(KNOWN_BAD_CLAUSE.fullmatch(part) for part in versions.split(","))


@dataclass(frozen=True)
class KnownBadVersion:
    """A tool release lintro refuses because it gives wrong results.

    A bad release can sit between good ones, so a minimum alone cannot
    exclude it (#3556). The specifier is parsed on construction: an entry
    that cannot be read is an error, never an entry that matches nothing.

    Attributes:
        versions: The bad release or range in the known-bad grammar
            (:data:`KNOWN_BAD_GRAMMAR`), e.g. ``"==2.7.0"`` or
            ``">=2.4.0,<2.6.0"``.
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
            ValueError: If a field is empty or ``versions`` does not follow
                the known-bad grammar.
        """
        for name in ("versions", "reason", "reference"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"known-bad entry needs a non-empty {name!r}")
        message = f"known-bad versions {self.versions!r} must be {KNOWN_BAD_GRAMMAR}"
        if not known_bad_versions_accepted(self.versions):
            raise ValueError(message)
        try:
            specifier = SpecifierSet(self.versions)
        except InvalidSpecifier as exc:
            raise ValueError(message) from exc
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
