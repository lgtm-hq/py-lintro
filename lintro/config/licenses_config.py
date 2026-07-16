"""Configuration models for dependency license compliance checking.

The configuration is intentionally self-contained (loaded independently from
``.lintro-config.yaml`` or ``[tool.lintro.licenses]`` in ``pyproject.toml``)
so the licenses feature does not couple to the main tiered config while other
work is in flight. A future change can fold this into a shared deps-policy
module (see issue #481).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lintro.utils.path_utils import find_file_upward

_YAML_FILENAMES = [
    ".lintro-config.yaml",
    ".lintro-config.yml",
    "lintro-config.yaml",
    "lintro-config.yml",
]

PolicyPreset = Literal["permissive", "copyleft-ok", "strict", "custom"]
UnknownPolicy = Literal["allow", "warn", "deny"]


class PackageException(BaseModel):
    """Per-package override for the license policy.

    Attributes:
        model_config: Pydantic model configuration.
        package: Distribution/package name the exception applies to.
        reason: Justification recorded for the audit trail.
        allowed: Force the package to be allowed regardless of its license.
        treat_as: Treat the package as if it declared this SPDX identifier.
    """

    model_config = ConfigDict(extra="forbid")

    package: str
    reason: str = ""
    allowed: bool = True
    treat_as: str | None = None


class LicensesConfig(BaseModel):
    """Policy configuration for dependency license compliance.

    Attributes:
        model_config: Pydantic model configuration.
        policy: Named preset controlling default allow/deny behavior.
        allowed: Additional SPDX identifiers to explicitly allow.
        denied: Additional SPDX identifiers to explicitly deny.
        unknown_policy: How to treat packages with an undeterminable license.
        exceptions: Per-package overrides.
        ignore_dev_dependencies: Skip development/test-only dependencies.
    """

    model_config = ConfigDict(extra="forbid")

    policy: PolicyPreset = "permissive"
    allowed: list[str] = Field(default_factory=list)
    denied: list[str] = Field(default_factory=list)
    unknown_policy: UnknownPolicy = "warn"
    exceptions: list[PackageException] = Field(default_factory=list)
    ignore_dev_dependencies: bool = True

    def exception_for(self, package_name: str) -> PackageException | None:
        """Return the exception matching a package name, if any.

        Matching is case-insensitive and normalizes underscores/hyphens so
        ``some_pkg`` and ``some-pkg`` are treated as equal.

        Args:
            package_name: Name of the package to look up.

        Returns:
            PackageException | None: The matching exception, or None.
        """
        key = _canonical_name(package_name)
        for exc in self.exceptions:
            if _canonical_name(exc.package) == key:
                return exc
        return None


def _canonical_name(name: str) -> str:
    """Normalize a package name for comparison.

    Args:
        name: Raw package name.

    Returns:
        str: Lower-cased name with underscores normalized to hyphens.
    """
    return name.strip().lower().replace("_", "-")


def _load_yaml_section(start_dir: Path) -> dict[str, Any] | None:
    """Load the ``licenses`` section from a ``.lintro-config.yaml`` file.

    Args:
        start_dir: Directory to begin the upward search from.

    Returns:
        dict[str, Any] | None: The raw licenses section, or None if absent.
    """
    try:
        import yaml
    except ImportError:
        return None

    config_path = find_file_upward(start_dir.resolve(), _YAML_FILENAMES)
    if config_path is None:
        return None

    data = yaml.safe_load(config_path.read_text()) or {}
    section = data.get("licenses")
    return section if isinstance(section, dict) else None


def _load_pyproject_section(start_dir: Path) -> dict[str, Any] | None:
    """Load ``[tool.lintro.licenses]`` from ``pyproject.toml``.

    Args:
        start_dir: Directory to begin the upward search from.

    Returns:
        dict[str, Any] | None: The raw licenses section, or None if absent.
    """
    pyproject = find_file_upward(start_dir.resolve(), ["pyproject.toml"])
    if pyproject is None:
        return None

    data = tomllib.loads(pyproject.read_text())
    section = data.get("tool", {}).get("lintro", {}).get("licenses")
    return section if isinstance(section, dict) else None


def load_licenses_config(start_dir: Path | None = None) -> LicensesConfig:
    """Load the license policy configuration.

    Resolution order (first hit wins): ``.lintro-config.yaml`` ``licenses:``
    section, then ``[tool.lintro.licenses]`` in ``pyproject.toml``. Falls back
    to permissive defaults when neither is present.

    Args:
        start_dir: Directory to search from. Defaults to the current directory.

    Returns:
        LicensesConfig: The resolved configuration.
    """
    base = Path(start_dir) if start_dir else Path.cwd()
    section = _load_yaml_section(base) or _load_pyproject_section(base)
    if not section:
        return LicensesConfig()
    return LicensesConfig.model_validate(section)
