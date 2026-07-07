"""Classification of version specifications across ecosystems.

The analyzer maps a raw version constraint (as written in a manifest) to a
:class:`~lintro.deps.models.VersionSpecType` and reports whether the constraint
caps the maximum installable version. Semantics differ per ecosystem — most
notably, a bare ``1.2.3`` is an exact pin in npm but a caret range in Cargo.
"""

from __future__ import annotations

import re

from lintro.deps.models import Ecosystem, VersionSpecType

__all__ = ["VersionAnalyzer"]

# Tokens that mean "no constraint at all".
_ANY_TOKENS = frozenset({"", "*", "x", "latest", "any"})

# A wildcard component such as ``1.2.*``, ``1.x`` or ``1.2.x``.
_WILDCARD_RE = re.compile(r"\.\*|(?:^|\.)x(?:\.|$)")


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
        normalized = spec.lower()

        if normalized in _ANY_TOKENS:
            return VersionSpecType.ANY

        # Wildcards are checked before operators so ``1.2.*`` wins over range.
        if self._is_wildcard(spec):
            return VersionSpecType.WILDCARD

        if spec.startswith("^"):
            return VersionSpecType.CARET

        if spec.startswith("~"):
            # ``~=`` (PEP 440) and ``~`` (npm/cargo) are both tilde ranges.
            return VersionSpecType.TILDE

        if spec.startswith("=="):
            return VersionSpecType.EXACT

        if spec.startswith("="):
            # Cargo exact pin (``=1.2.3``); npm treats ``=`` as exact too.
            return VersionSpecType.EXACT

        # Multi-clause or comparator constraints (``>=1,<2``, ``>=1``).
        if any(op in spec for op in (">", "<", ",")):
            if self.has_upper_bound(spec, ecosystem):
                return VersionSpecType.RANGE
            return VersionSpecType.UNBOUNDED

        # A bare version number. Cargo treats it as caret; npm/python as exact.
        if ecosystem is Ecosystem.CARGO:
            return VersionSpecType.CARET
        return VersionSpecType.EXACT

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
        spec_type = self._quick_type(spec, ecosystem)

        # These forms inherently cap the upper bound.
        if spec_type in {
            VersionSpecType.EXACT,
            VersionSpecType.TILDE,
            VersionSpecType.CARET,
            VersionSpecType.WILDCARD,
        }:
            return True
        if spec_type is VersionSpecType.ANY:
            return False

        # Comparator constraints: an upper bound needs ``<`` or ``<=``, or a
        # ``==``/``=`` exact clause somewhere in the expression.
        if "<" in spec:
            return True
        clauses = re.split(r"[,\s]+", spec)
        for clause in clauses:
            token = clause.strip()
            if token.startswith("==") or (
                token.startswith("=") and not token.startswith("==")
            ):
                return True
        return False

    def _quick_type(self, spec: str, ecosystem: Ecosystem) -> VersionSpecType:
        """Classify without recursing into :meth:`has_upper_bound`.

        Args:
            spec: Stripped version constraint.
            ecosystem: Ecosystem governing the constraint semantics.

        Returns:
            VersionSpecType: A best-effort classification.
        """
        normalized = spec.lower()
        if normalized in _ANY_TOKENS:
            return VersionSpecType.ANY
        if self._is_wildcard(spec):
            return VersionSpecType.WILDCARD
        if spec.startswith("^"):
            return VersionSpecType.CARET
        if spec.startswith("~"):
            return VersionSpecType.TILDE
        if spec.startswith("=="):
            return VersionSpecType.EXACT
        if spec.startswith("="):
            return VersionSpecType.EXACT
        if any(op in spec for op in (">", "<", ",")):
            return VersionSpecType.RANGE
        if ecosystem is Ecosystem.CARGO:
            return VersionSpecType.CARET
        return VersionSpecType.EXACT

    def _is_wildcard(self, spec: str) -> bool:
        """Return whether the spec uses a wildcard component.

        Args:
            spec: Stripped version constraint.

        Returns:
            bool: ``True`` for patterns like ``1.2.*`` or ``1.x``.
        """
        return bool(_WILDCARD_RE.search(spec))
