"""Dependency version policy validation for the ``lintro deps`` command.

This package parses dependency manifests, classifies each version
specification, and validates the specs against a configurable policy.
"""

from lintro.deps.models import (
    Dependency,
    DepsCheckResult,
    Ecosystem,
    VersionSpecType,
    VersionViolation,
)
from lintro.deps.policy_engine import PolicyEngine
from lintro.deps.version_analyzer import VersionAnalyzer

__all__ = [
    "Dependency",
    "DepsCheckResult",
    "Ecosystem",
    "PolicyEngine",
    "VersionAnalyzer",
    "VersionSpecType",
    "VersionViolation",
]
