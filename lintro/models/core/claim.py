"""Claim model: the file patterns a tool touches and what it does to them.

A :class:`Claim` pairs a set of glob patterns with the capabilities the tool
applies to files matching them. Declaring claims replaces the scalar
``priority`` integer as the input to execution ordering (#1735): the order
becomes *derived* from what each tool does rather than authored as a number
nothing validates.

This module is data only. Nothing reads claims for ordering yet — step 2 of
the epic lands the declarations so step 3 can diff the derived order against
the current one before any behaviour flips.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.enums.capability import MUTATING_CAPABILITIES, Cap


@dataclass(frozen=True)
class Claim:
    """A set of file patterns and the capabilities a tool applies to them.

    Attributes:
        patterns: Glob patterns the claim covers (e.g. ``["*.py", "*.pyi"]``).
            An empty list is a *project-scoped* claim: the tool does its own
            discovery and is not addressed by pattern (osv-scanner).
        capabilities: What the tool does to matching files. Must be non-empty.
    """

    patterns: list[str] = field(default_factory=list)
    capabilities: set[Cap] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Validate the claim.

        Raises:
            ValueError: If the claim declares no capabilities, or a pattern is
                empty or blank.
        """
        if not self.capabilities:
            raise ValueError("Claim must declare at least one capability")
        for pattern in self.patterns:
            if not pattern or not pattern.strip():
                raise ValueError("Claim patterns must be non-empty strings")

    @property
    def is_mutating(self) -> bool:
        """Report whether this claim rewrites the files it covers.

        Returns:
            True when the claim holds ``FIX`` or ``FORMAT``.
        """
        return bool(self.capabilities & MUTATING_CAPABILITIES)

    @property
    def mutating_capabilities(self) -> set[Cap]:
        """Return the mutating subset of this claim's capabilities.

        Used by the authority rule "fewest mutating capabilities wins", which
        lets a dedicated formatter outrank a multi-capability tool.

        Returns:
            The intersection of the claim's capabilities with ``FIX``/``FORMAT``.
        """
        return set(self.capabilities & MUTATING_CAPABILITIES)
