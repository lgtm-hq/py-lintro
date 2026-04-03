"""Cargo install strategy."""

from __future__ import annotations

from lintro.enums.install_context import PackageManager
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.registry import register_strategy


class CargoStrategy(InstallStrategy):
    """Install strategy for Rust crates installed via cargo."""

    def install_type(self) -> str:
        """Return ``'cargo'``."""
        return "cargo"

    def is_available(self, env: InstallEnvironment) -> bool:
        """Available if cargo is on PATH."""
        return env.has(PackageManager.CARGO)

    def check_prerequisites(
        self,
        env: InstallEnvironment,
        _tool_name: str,
    ) -> str | None:
        """Return skip reason if cargo is not available.

        Args:
            env: The current install environment.
            _tool_name: Canonical tool name (unused).

        Returns:
            Skip reason or None.
        """
        if not env.has(PackageManager.CARGO):
            return "cargo not available (install Rust first)"
        return None

    def install_hint(
        self,
        _env: InstallEnvironment,
        tool_name: str,
        _tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate cargo install hint.

        Args:
            _env: The current install environment.
            tool_name: Canonical tool name.
            _tool_version: Expected version.
            install_package: Package name override.
            _install_component: Unused for cargo.

        Returns:
            Shell command string.
        """
        pkg = install_package or tool_name
        return f"cargo install {pkg}"

    def upgrade_hint(
        self,
        _env: InstallEnvironment,
        tool_name: str,
        _tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate cargo upgrade hint with --force flag.

        Args:
            _env: The current install environment.
            tool_name: Canonical tool name.
            _tool_version: Expected version.
            install_package: Package name override.
            _install_component: Unused for cargo.

        Returns:
            Shell command string.
        """
        pkg = install_package or tool_name
        return f"cargo install --force {pkg}"


register_strategy(CargoStrategy())
