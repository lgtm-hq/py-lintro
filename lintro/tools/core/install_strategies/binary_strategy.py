"""Binary download install strategy."""

from __future__ import annotations

from lintro.enums.install_context import PackageManager
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.package_names import (
    brew_formula_name,
    ecosystem_package_name,
)
from lintro.tools.core.install_strategies.registry import register_strategy


class BinaryStrategy(InstallStrategy):
    """Install strategy for standalone binary tools (brew or manual download)."""

    def install_type(self) -> str:
        """Return ``'binary'``."""
        return "binary"

    def is_available(self, _env: InstallEnvironment) -> bool:
        """Binary tools always have a fallback (manual URL)."""
        return True

    def check_prerequisites(
        self,
        _env: InstallEnvironment,
        _tool_name: str,
    ) -> str | None:
        """Binary tools never fail prerequisite checks.

        Args:
            _env: The current install environment (unused).
            _tool_name: Canonical tool name (unused).

        Returns:
            Always None.
        """
        return None

    def install_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        _tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate binary install hint.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            _tool_version: Expected version (unused).
            install_package: Package name override.
            _install_component: Unused for binary.

        Returns:
            Shell command string.
        """
        pkg = ecosystem_package_name(tool_name, install_package)
        if env.has(PackageManager.BREW):
            brew_pkg = brew_formula_name(tool_name, install_package)
            return f"brew install {brew_pkg}"
        return f"See https://github.com/search?q={pkg}+releases"

    def upgrade_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        _tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate binary upgrade hint.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            _tool_version: Expected version (unused).
            install_package: Package name override.
            _install_component: Unused for binary.

        Returns:
            Shell command string.
        """
        pkg = ecosystem_package_name(tool_name, install_package)
        if env.has(PackageManager.BREW):
            brew_pkg = brew_formula_name(tool_name, install_package)
            return f"brew upgrade {brew_pkg}"
        return f"See https://github.com/search?q={pkg}+releases"


register_strategy(BinaryStrategy())
