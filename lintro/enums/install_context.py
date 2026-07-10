"""Installation context and CI system enums for lintro.

Identifies how lintro itself was installed and which CI environment
it is running in.
"""

from __future__ import annotations

import os
from enum import StrEnum, auto


class InstallContext(StrEnum):
    """How lintro itself was installed."""

    HOMEBREW_FULL = auto()
    HOMEBREW_BIN = auto()
    NPM_BIN = auto()
    PIP = auto()
    DOCKER = auto()
    DEVELOPMENT = auto()


class PackageManager(StrEnum):
    """Known package managers that can install lintro's external tools."""

    BREW = auto()
    BUN = auto()
    CARGO = auto()
    NPM = auto()
    PIP = auto()
    RUSTUP = auto()
    UV = auto()


class CISystem(StrEnum):
    """Known CI/CD systems detected via environment variables."""

    GITHUB_ACTIONS = "GitHub Actions"
    GITLAB_CI = "GitLab CI"
    CIRCLECI = "CircleCI"
    JENKINS = "Jenkins"
    BUILDKITE = "Buildkite"
    AZURE_PIPELINES = "Azure Pipelines"

    @classmethod
    def detect(cls) -> CISystem | None:
        """Detect the current CI system from environment variables.

        Returns:
            The matching CISystem member, or None if not in CI.
        """
        for env_var, system in _CI_ENV_MAP.items():
            if os.environ.get(env_var):
                return system
        return None


# Mapping from env var to CISystem — kept outside the class to avoid
# StrEnum treating it as a member.
_CI_ENV_MAP: dict[str, CISystem] = {
    "GITHUB_ACTIONS": CISystem.GITHUB_ACTIONS,
    "GITLAB_CI": CISystem.GITLAB_CI,
    "CIRCLECI": CISystem.CIRCLECI,
    "JENKINS_URL": CISystem.JENKINS,
    "BUILDKITE": CISystem.BUILDKITE,
    "TF_BUILD": CISystem.AZURE_PIPELINES,
}
