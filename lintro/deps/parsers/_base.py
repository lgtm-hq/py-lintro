"""Shared helpers for dependency manifest parsers."""

from __future__ import annotations

from lintro.deps.models import Dependency, Ecosystem
from lintro.deps.version_analyzer import VersionAnalyzer

__all__ = ["build_dependency"]

_ANALYZER = VersionAnalyzer()


def build_dependency(
    *,
    name: str,
    version_spec: str,
    ecosystem: Ecosystem,
    file: str,
    line: int | None = None,
) -> Dependency:
    """Classify a raw version spec and build a :class:`Dependency`.

    Args:
        name: Package name.
        version_spec: Raw version constraint from the manifest.
        ecosystem: Ecosystem governing the constraint semantics.
        file: Manifest file the dependency was parsed from.
        line: 1-based line number within the manifest, when known.

    Returns:
        Dependency: The classified dependency.
    """
    spec = version_spec.strip()
    spec_type = _ANALYZER.classify(spec, ecosystem)
    has_upper = _ANALYZER.has_upper_bound(spec, ecosystem)
    return Dependency(
        name=name,
        version_spec=spec,
        spec_type=spec_type,
        ecosystem=ecosystem,
        has_upper_bound=has_upper,
        file=file,
        line=line,
    )
