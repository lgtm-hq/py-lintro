"""Rustup install strategy."""

from __future__ import annotations

from lintro.enums.install_context import PackageManager
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.registry import register_strategy


class RustupStrategy(InstallStrategy):
    """Install strategy for Rust toolchain components via rustup."""

    def install_type(self) -> str:
        """Return ``'rustup'``."""
        return "rustup"

    def is_available(self, env: InstallEnvironment) -> bool:
        """Available if rustup is on PATH."""
        return env.has(PackageManager.RUSTUP)

    def check_prerequisites(
        self,
        env: InstallEnvironment,
        tool_name: str,
    ) -> str | None:
        """Return skip reason if rustup is not available.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.

        Returns:
            Skip reason or None.
        """
        if not env.has(PackageManager.RUSTUP):
            return f"{tool_name}: rustup not available (install Rust first)"
        return None

    def install_hint(
        self,
        _env: InstallEnvironment,
        _tool_name: str,
        _tool_version: str,
        _install_package: str | None,
        install_component: str | None,
    ) -> str:
        """Generate rustup install hint.

        Args:
            _env: The current install environment (unused).
            _tool_name: Canonical tool name (unused).
            _tool_version: Expected version (unused).
            _install_package: Unused for rustup.
            install_component: Rustup component name (e.g., ``"clippy"``).

        Returns:
            Shell command string.
        """
        if install_component:
            import shlex

            return f"rustup component add {shlex.quote(install_component)}"
        return "rustup toolchain install stable"

    def upgrade_hint(
        self,
        _env: InstallEnvironment,
        _tool_name: str,
        _tool_version: str,
        _install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate rustup upgrade hint.

        Args:
            _env: The current install environment (unused).
            _tool_name: Canonical tool name (unused).
            _tool_version: Expected version (unused).
            _install_package: Unused for rustup.
            _install_component: Unused for upgrade.

        Returns:
            Shell command string.
        """
        return "rustup update stable"


register_strategy(RustupStrategy())
