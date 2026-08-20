"""RubyGems install strategy."""

from __future__ import annotations

from lintro.enums.install_context import PackageManager
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.package_names import ecosystem_package_name
from lintro.tools.core.install_strategies.registry import register_strategy


class GemStrategy(InstallStrategy):
    """Install strategy for Ruby gems installed via ``gem``."""

    def install_type(self) -> str:
        """Return ``'gem'``.

        Returns:
            The install_type identifier this strategy handles.
        """
        return "gem"

    def is_available(self, env: InstallEnvironment) -> bool:
        """Report whether ``gem`` is on PATH.

        Args:
            env: The current install environment.

        Returns:
            True when the RubyGems CLI is available.
        """
        return env.has(PackageManager.GEM)

    def check_prerequisites(
        self,
        env: InstallEnvironment,
        _tool_name: str,
    ) -> str | None:
        """Return a skip reason when ``gem`` is not available.

        Args:
            env: The current install environment.
            _tool_name: Canonical tool name (unused).

        Returns:
            Skip reason or None.
        """
        if not env.has(PackageManager.GEM):
            return "gem not available (install Ruby first)"
        return None

    def install_hint(
        self,
        _env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate a ``gem install`` hint pinned to the expected version.

        Args:
            _env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            _install_component: Unused for gems.

        Returns:
            Shell command string.
        """
        pkg = ecosystem_package_name(tool_name, install_package)
        return f"gem install {pkg} --version {tool_version} --no-document"

    def upgrade_hint(
        self,
        _env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        _install_component: str | None,
    ) -> str:
        """Generate a ``gem install`` upgrade hint for the expected version.

        RubyGems installs new versions side by side, so the upgrade command is
        the pinned install command.

        Args:
            _env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            _install_component: Unused for gems.

        Returns:
            Shell command string.
        """
        pkg = ecosystem_package_name(tool_name, install_package)
        return f"gem install {pkg} --version {tool_version} --no-document"


register_strategy(GemStrategy())
