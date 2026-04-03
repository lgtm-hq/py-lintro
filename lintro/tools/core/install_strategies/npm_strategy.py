"""npm/bun install strategy."""

from __future__ import annotations

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.brew_names import BREW_FORMULA_NAMES
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.registry import register_strategy


class NpmStrategy(InstallStrategy):
    """Install strategy for npm/bun-managed JavaScript packages."""

    def install_type(self) -> str:
        """Return ``'npm'``."""
        return "npm"

    def is_available(self, env: InstallEnvironment) -> bool:
        """Available if bun, npm, or brew exists."""
        return (
            env.has(PackageManager.BUN)
            or env.has(PackageManager.NPM)
            or env.has(PackageManager.BREW)
        )

    def check_prerequisites(
        self,
        env: InstallEnvironment,
        tool_name: str,
    ) -> str | None:
        """Return skip reason if no JS package manager is available.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.

        Returns:
            Skip reason or None.
        """
        if env.has(PackageManager.BUN) or env.has(PackageManager.NPM):
            return None
        if env.has(PackageManager.BREW) and tool_name in BREW_FORMULA_NAMES:
            return None
        return "bun/npm not available (install Node.js first)"

    def install_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate npm install hint.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            _install_component: Unused for npm.

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
                or not (env.has(PackageManager.BUN) or env.has(PackageManager.NPM))
            )
        ):
            return f"brew install {brew_pkg}"
        return f"{_npm_cmd(env)} {pkg}@{tool_version}"

    def upgrade_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate npm upgrade hint (npm replaces on install).

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            _install_component: Unused for npm.

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
                or not (env.has(PackageManager.BUN) or env.has(PackageManager.NPM))
            )
        ):
            return f"brew upgrade {brew_pkg}"
        return f"{_npm_cmd(env)} {pkg}@{tool_version}"


def _npm_cmd(env: InstallEnvironment) -> str:
    """Return the preferred npm install command prefix."""
    return "bun add -g" if env.has(PackageManager.BUN) else "npm install -g"


def _is_homebrew_context(env: InstallEnvironment) -> bool:
    """Check if the environment is a Homebrew install with brew available."""
    return env.has(PackageManager.BREW) and env.install_context in (
        InstallContext.HOMEBREW_FULL,
        InstallContext.HOMEBREW_BIN,
    )


register_strategy(NpmStrategy())
