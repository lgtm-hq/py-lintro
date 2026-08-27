"""Classification of version specifications across ecosystems.

The analyzer maps a raw version constraint (as written in a manifest) to a
:class:`~lintro.deps.models.VersionSpecType` and reports whether the constraint
caps the maximum installable version. Semantics differ per ecosystem — most
notably, a bare ``1.2.3`` is an exact pin in npm but a caret range in Cargo.

Alternative clauses (npm ``a || b``) are always evaluated clause by clause: the
expression is bounded only when *every* alternative is bounded, since npm is
free to resolve any one of them.
"""

from __future__ import annotations

import re

from lintro.deps.models import Ecosystem, VersionSpecType

__all__ = ["VersionAnalyzer"]

# Tokens that mean "no constraint at all".
_ANY_TOKENS = frozenset({"", "*", "x", "latest", "any"})

# A wildcard component such as ``1.2.*``, ``1.x`` or ``1.2.x``.
_WILDCARD_RE = re.compile(r"\.\*|(?:^|\.)x(?:\.|$)")

# An npm hyphen range (``1.2.3 - 2.3.4``), which requires surrounding spaces
# and is therefore distinguishable from a prerelease tag like ``1.2.3-alpha``.
_HYPHEN_RANGE_RE = re.compile(r"^\S+\s+-\s+\S+$")

# Types that inherently cap the maximum installable version.
_INHERENTLY_BOUNDED = frozenset(
    {
        VersionSpecType.EXACT,
        VersionSpecType.TILDE,
        VersionSpecType.CARET,
        VersionSpecType.WILDCARD,
    },
)


class VersionAnalyzer:
    """Classify version specifications and detect upper bounds."""

    def classify(
        self,
        version_spec: str,
        ecosystem: Ecosystem,
    ) -> VersionSpecType:
        """Classify a version specification by type.

        Args:
            version_spec: Raw version constraint from the manifest.
            ecosystem: Ecosystem governing the constraint semantics.

        Returns:
            VersionSpecType: The classified spec type.
        """
        spec = version_spec.strip()

        if spec.lower() in _ANY_TOKENS:
            return VersionSpecType.ANY

        # Alternatives are handled before any single-clause heuristic so that a
        # wildcard or comparator on one side cannot classify the whole spec.
        if "||" in spec:
            if self.has_upper_bound(spec, ecosystem):
                return VersionSpecType.RANGE
            return VersionSpecType.UNBOUNDED

        spec_type = self._quick_type(spec, ecosystem)
        if spec_type is VersionSpecType.RANGE and not self.has_upper_bound(
            spec,
            ecosystem,
        ):
            return VersionSpecType.UNBOUNDED
        return spec_type

    def has_upper_bound(
        self,
        version_spec: str,
        ecosystem: Ecosystem,
    ) -> bool:
        """Report whether a version spec caps the maximum version.

        Args:
            version_spec: Raw version constraint from the manifest.
            ecosystem: Ecosystem governing the constraint semantics.

        Returns:
            bool: ``True`` when the maximum installable version is bounded.
        """
        spec = version_spec.strip()

        # Every alternative must be bounded; one unbounded clause is enough for
        # the resolver to float to a future major.
        if "||" in spec:
            clauses = [part.strip() for part in spec.split("||") if part.strip()]
            return bool(clauses) and all(
                self.has_upper_bound(clause, ecosystem) for clause in clauses
            )

        spec_type = self._quick_type(spec, ecosystem)

        if spec_type in _INHERENTLY_BOUNDED:
            return True
        if spec_type in {VersionSpecType.ANY, VersionSpecType.UNBOUNDED}:
            return False
        if self._is_hyphen_range(spec, ecosystem):
            return True

        # Comparator constraints: an upper bound needs ``<`` or ``<=``, or a
        # ``==``/``=`` exact clause somewhere in the expression.
        if "<" in spec:
            return True
        for clause in re.split(r"[,\s]+", spec):
            if clause.strip().startswith("="):
                return True
        return False

    def _quick_type(self, spec: str, ecosystem: Ecosystem) -> VersionSpecType:
        """Classify a single (alternative-free) clause.

        ``RANGE`` here means "comparator or hyphen expression"; the caller
        decides whether it is actually bounded via :meth:`has_upper_bound`.

        Args:
            spec: Stripped version constraint without ``||`` alternatives.
            ecosystem: Ecosystem governing the constraint semantics.

        Returns:
            VersionSpecType: A best-effort classification.
        """
        normalized = spec.lower()
        if normalized in _ANY_TOKENS:
            return VersionSpecType.ANY
        # ``1.2.3 - 2.3.4`` is a bounded range, not a bare exact version.
        if self._is_hyphen_range(spec, ecosystem):
            return VersionSpecType.RANGE
        if self._is_wildcard(spec):
            return VersionSpecType.WILDCARD
        if spec.startswith("^"):
            return VersionSpecType.CARET
        if spec.startswith("~"):
            # ``~=`` (PEP 440) and ``~`` (npm/cargo) are both tilde ranges.
            return VersionSpecType.TILDE
        # Exclusion-only specs (``!=1.2.3``) are unbounded, not exact pins.
        if spec.startswith("!=") or spec.startswith("≠"):
            return VersionSpecType.UNBOUNDED
        if spec.startswith("=="):
            return VersionSpecType.EXACT
        if spec.startswith("="):
            # Cargo exact pin (``=1.2.3``); npm treats ``=`` as exact too.
            return VersionSpecType.EXACT
        # Multi-clause or comparator constraints (``>=1,<2``, ``>=1``).
        if any(op in spec for op in (">", "<", ",", "!=")):
            return VersionSpecType.RANGE
        # A bare version number. Cargo treats it as caret; npm/python as exact.
        if ecosystem is Ecosystem.CARGO:
            return VersionSpecType.CARET
        return VersionSpecType.EXACT

    @staticmethod
    def _is_hyphen_range(spec: str, ecosystem: Ecosystem) -> bool:
        """Return whether the spec is an npm hyphen range.

        Args:
            spec: Stripped version constraint.
            ecosystem: Ecosystem governing the constraint semantics.

        Returns:
            bool: ``True`` for npm specs like ``1.2.3 - 2.3.4``.
        """
        if ecosystem is not Ecosystem.NPM:
            return False
        return bool(_HYPHEN_RANGE_RE.match(spec))

    def _is_wildcard(self, spec: str) -> bool:
        """Return whether the spec uses a wildcard component.

        Args:
            spec: Stripped version constraint.

        Returns:
            bool: ``True`` for patterns like ``1.2.*`` or ``1.x``.
        """
        return bool(_WILDCARD_RE.search(spec.lower()))
