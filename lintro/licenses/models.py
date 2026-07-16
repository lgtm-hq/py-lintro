"""Data models for dependency license compliance checking."""

from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field


class LicenseStatus(StrEnum):
    """Compliance status of a package's license against the active policy.

    Attributes:
        ALLOWED: License is explicitly permitted by the policy.
        DENIED: License is explicitly forbidden by the policy.
        UNKNOWN: License could not be determined or is not recognized.
    """

    ALLOWED = auto()
    DENIED = auto()
    UNKNOWN = auto()


class PackageLicense(BaseModel):
    """License information for a single resolved dependency.

    Attributes:
        model_config: Pydantic model configuration.
        name: Distribution/package name.
        version: Resolved package version.
        license_id: Normalized SPDX identifier, if one could be determined.
        license_name: Raw license string as reported by the ecosystem.
        source_file: Manifest or metadata source the package was read from.
        ecosystem: Package ecosystem (e.g. ``python``, ``npm``).
        is_dev: Whether the package is a development/test-only dependency.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    license_id: str | None = None
    license_name: str | None = None
    source_file: str = ""
    ecosystem: str = "python"
    is_dev: bool = False


class LicenseResult(BaseModel):
    """Result of applying the license policy to a single package.

    Attributes:
        model_config: Pydantic model configuration.
        package: The package that was evaluated.
        status: Compliance status assigned by the policy engine.
        reason: Human-readable explanation for the assigned status.
    """

    model_config = ConfigDict(frozen=True)

    package: PackageLicense
    status: LicenseStatus
    reason: str = Field(default="")

    @property
    def is_violation(self) -> bool:
        """Whether this result represents a policy violation.

        Returns:
            bool: True if the status is DENIED, otherwise False.
        """
        return self.status is LicenseStatus.DENIED
