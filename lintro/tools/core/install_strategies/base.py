"""Abstract base for install-type strategy classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from lintro.tools.core.install_strategies.environment import InstallEnvironment

if TYPE_CHECKING:
    from lintro.tools.core.manifest_models import ManifestTool


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

    def tool_hint(
        self,
        env: InstallEnvironment,
        tool: ManifestTool,
        *,
        upgrade: bool,
    ) -> str:
        """Generate the install or upgrade command for a manifest entry.

        Callers holding a whole manifest entry use this instead of unpacking
        it, so a strategy that needs more of the entry than the hint
        arguments carry (the npm strategy reads ``min_version``, #2045) can
        take it without changing every strategy's signature.

        Args:
            env: The current install environment.
            tool: Manifest entry of the tool.
            upgrade: Whether to plan an upgrade rather than an install.

        Returns:
            Shell command string, or a manual-action message.
        """
        hint = self.upgrade_hint if upgrade else self.install_hint
        return hint(
            env,
            tool.name,
            tool.version,
            tool.install_package,
            tool.install_component,
        )
