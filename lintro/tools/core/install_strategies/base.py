"""Abstract base for install-type strategy classes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from lintro.tools.core.install_strategies.environment import InstallEnvironment


class InstallStrategy(ABC):
    """Strategy for a single install type (pip, npm, binary, cargo, rustup).

    Each subclass knows how to check prerequisites, generate install
    commands, and generate upgrade commands for its ecosystem.
    """

    @abstractmethod
    def install_type(self) -> str:
        """Return the install_type string this strategy handles.

        Returns:
            Identifier such as ``"pip"`` or ``"npm"``.
        """
        ...

    @abstractmethod
    def is_available(self, env: InstallEnvironment) -> bool:
        """Check if the required package manager is available.

        Args:
            env: The current install environment.

        Returns:
            True if this strategy can execute installs.
        """
        ...

    @abstractmethod
    def check_prerequisites(
        self,
        env: InstallEnvironment,
        tool_name: str,
    ) -> str | None:
        """Check if prerequisites for installing via this strategy are met.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name (needed to check brew formula maps).

        Returns:
            A skip-reason string if prerequisites are NOT met, or None if OK.
        """
        ...

    @abstractmethod
    def install_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        install_component: str | None,
    ) -> str:
        """Generate a context-aware install command string.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            install_component: Rustup component name.

        Returns:
            Shell command string to install the tool.
        """
        ...

    @abstractmethod
    def upgrade_hint(
        self,
        env: InstallEnvironment,
        tool_name: str,
        tool_version: str,
        install_package: str | None,
        install_component: str | None,
    ) -> str:
        """Generate a context-aware upgrade command string.

        Args:
            env: The current install environment.
            tool_name: Canonical tool name.
            tool_version: Expected version.
            install_package: Package name override.
            install_component: Rustup component name.

        Returns:
            Shell command string to upgrade the tool.
        """
        ...
