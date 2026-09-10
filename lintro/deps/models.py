"""Data models for dependency version policy validation."""

from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Dependency",
    "DepsCheckResult",
    "Ecosystem",
    "VersionSpecType",
    "VersionViolation",
]


class Ecosystem(StrEnum):
    """Dependency ecosystem, which governs version-spec semantics.

    Attributes:
        PYTHON: PEP 440 / Poetry constraints (``pyproject.toml``, requirements).
        NPM: npm/semver constraints (``package.json``).
        CARGO: Cargo semver constraints (``Cargo.toml``).
    """

    PYTHON = auto()
    NPM = auto()
    CARGO = auto()


class VersionSpecType(StrEnum):
    """Classification of a version specification.

    Attributes:
        EXACT: Pinned to a single version (``==1.2.3``, npm ``1.2.3``).
        TILDE: Patch-level updates only (``~=1.2.3``, ``~1.2.3``).
        CARET: Minor-level updates only (``^1.2.3``).
        RANGE: Bounded range with an upper limit (``>=1.2.0,<2.0.0``).
        UNBOUNDED: Lower bound only, no upper limit (``>=1.0.0``).
        WILDCARD: Wildcard pattern (``1.2.*``, ``1.x``).
        ANY: No effective constraint (``*``, empty string).
    """

    EXACT = auto()
    TILDE = auto()
    CARET = auto()
    RANGE = auto()
    UNBOUNDED = auto()
    WILDCARD = auto()
    ANY = auto()


class Dependency(BaseModel):
    """A single parsed dependency and its classified version spec.

    Attributes:
        model_config: Pydantic model configuration.
        name: Package name.
        version_spec: Raw version specification as written in the manifest.
        spec_type: Classified version-spec type.
        ecosystem: Ecosystem the dependency belongs to.
        has_upper_bound: Whether the spec caps the maximum version.
        file: Manifest file the dependency was parsed from.
        line: 1-based line number within the manifest, when known.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    name: str
    version_spec: str
    spec_type: VersionSpecType
    ecosystem: Ecosystem
    has_upper_bound: bool
    file: str
    line: int | None = None


class VersionViolation(BaseModel):
    """A policy violation raised against a single dependency.

    Attributes:
        model_config: Pydantic model configuration.
        dependency: Dependency that violated the policy.
        rule: Identifier of the rule that was violated.
        message: Human-readable description of the violation.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    dependency: Dependency
    rule: str
    message: str


class DepsCheckResult(BaseModel):
    """Aggregate result of a dependency policy check.

    Attributes:
        model_config: Pydantic model configuration.
        dependencies: All dependencies that were analyzed.
        violations: Policy violations found across all dependencies.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    dependencies: list[Dependency] = Field(default_factory=list)
    violations: list[VersionViolation] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return whether the check produced no violations.

        Returns:
            bool: ``True`` when there are no violations.
        """
        return not self.violations
