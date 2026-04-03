"""Pip/uv install strategy."""

from __future__ import annotations

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.brew_names import BREW_FORMULA_NAMES
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.registry import register_strategy


class PipStrategy(InstallStrategy):
    """Install strategy for pip/uv-managed Python packages."""

    def install_type(self) -> str:
        """Return ``'pip'``."""
        return "pip"

    def is_available(self, env: InstallEnvironment) -> bool:
        """Available if uv, pip, or brew (for formula-mapped tools) exists."""
        return (
            env.has(PackageManager.UV)
            or env.has(PackageManager.PIP)
            or env.has(PackageManager.BREW)
        )

    def check_prerequisites(
        self,
        env: InstallEnvironment,
        tool_name: str,
    ) -> str | None:
        """Return skip reason if neither uv nor pip is available.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.

        Returns:
            Skip reason or None.
        """
        if env.has(PackageManager.UV) or env.has(PackageManager.PIP):
            return None
        if env.has(PackageManager.BREW) and tool_name in BREW_FORMULA_NAMES:
            return None
        return "uv/pip not available"

    def install_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        install_component: str | None,
    ) -> str:
        """Generate pip install hint.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            install_component: Unused for pip.

        Returns:
            Shell command string.
        """
        pkg = install_package or tool_name
        brew_pkg = BREW_FORMULA_NAMES.get(tool_name)
        if (
            brew_pkg
            and env.has(PackageManager.BREW)
            and (
                _is_homebrew_context(env)
                or not (env.has(PackageManager.UV) or env.has(PackageManager.PIP))
            )
        ):
            return f"brew install {brew_pkg}"
        if not (env.has(PackageManager.UV) or env.has(PackageManager.PIP)):
            return f"Install {tool_name} via pip/uv (neither found in PATH)"
        return f"{_pip_cmd(env)} '{pkg}>={tool_version}'"

    def upgrade_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        install_component: str | None,
    ) -> str:
        """Generate pip upgrade hint.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            install_component: Unused for pip.

        Returns:
            Shell command string.
        """
        pkg = install_package or tool_name
        brew_pkg = BREW_FORMULA_NAMES.get(tool_name)
        if (
            brew_pkg
            and env.has(PackageManager.BREW)
            and (
                _is_homebrew_context(env)
                or not (env.has(PackageManager.UV) or env.has(PackageManager.PIP))
            )
        ):
            return f"brew upgrade {brew_pkg}"
        if not (env.has(PackageManager.UV) or env.has(PackageManager.PIP)):
            return f"Upgrade {tool_name} via pip/uv (neither found in PATH)"
        return f"{_pip_cmd(env)} --upgrade '{pkg}>={tool_version}'"


def _pip_cmd(env: InstallEnvironment) -> str:
    """Return the preferred pip install command prefix."""
    return "uv pip install" if env.has(PackageManager.UV) else "pip install"


def _is_homebrew_context(env: InstallEnvironment) -> bool:
    """Check if the environment is a Homebrew install with brew available."""
    return env.has(PackageManager.BREW) and env.install_context in (
        InstallContext.HOMEBREW_FULL,
        InstallContext.HOMEBREW_BIN,
    )


register_strategy(PipStrategy())
