"""Configuration models for the ``lintro deps`` command.

The dependency policy validator enforces version-specification rules across
dependency manifests (``pyproject.toml``, ``requirements.txt``,
``package.json``, ``Cargo.toml``). Policies are expressed either through one of
the built-in presets (``strict``, ``flexible``, ``loose``) or via a ``custom``
policy that reads the explicit rule fields on :class:`DepsConfig`.

Example ``.lintro-config.yaml``::

    deps:
      policy: flexible
      exceptions:
        - package: "boto3"
          allowed_types: [exact]
          reason: "AWS SDK versions are tightly coupled"
"""

from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DepsConfig",
    "DepsPolicy",
    "PackageException",
]


class DepsPolicy(StrEnum):
    """Named policy presets for dependency version validation.

    Attributes:
        STRICT: Require exact pins for maximum reproducibility.
        FLEXIBLE: Allow bounded ranges; require an upper bound.
        LOOSE: Accept any constraint; only flag fully unconstrained specs.
        CUSTOM: Use the explicit rule fields on :class:`DepsConfig`.
    """

    STRICT = auto()
    FLEXIBLE = auto()
    LOOSE = auto()
    CUSTOM = auto()


class PackageException(BaseModel):
    """Per-package override of the active policy.

    Attributes:
        model_config: Pydantic model configuration.
        package: Package name or ``fnmatch`` glob (e.g. ``aws-*``).
        allowed_types: Version-spec types permitted for the matched package.
        require_upper_bound: Optional override of the upper-bound requirement.
        reason: Human-readable justification for the exception.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    package: str
    allowed_types: list[str] = Field(default_factory=list)
    require_upper_bound: bool | None = None
    reason: str | None = None


class DepsConfig(BaseModel):
    """Configuration for dependency version policy validation.

    Attributes:
        model_config: Pydantic model configuration.
        policy: Active policy preset (or ``custom``).
        require_upper_bound: Whether specs must cap the upper version bound.
        allowed_types: Version-spec types allowed under a ``custom`` policy.
        disallowed_types: Version-spec types forbidden under a ``custom`` policy.
        exceptions: Per-package overrides applied before the base policy.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    policy: DepsPolicy = DepsPolicy.FLEXIBLE
    require_upper_bound: bool = True
    allowed_types: list[str] = Field(
        default_factory=lambda: ["exact", "tilde", "caret", "range", "wildcard"],
    )
    disallowed_types: list[str] = Field(
        default_factory=lambda: ["any", "unbounded"],
    )
    exceptions: list[PackageException] = Field(default_factory=list)
