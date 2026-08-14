"""Install environment detection for strategy-based tool installation.

Provides a slim, frozen data object describing what package managers are
available on the current system.  Strategy classes receive this instead of
the full ``RuntimeContext`` so they stay decoupled from CI detection,
platform labels, and other unrelated concerns.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.enums.node_manager_source import NodeManagerSource
from lintro.tools.core.install_strategies.node_project import (
    NodeProject,
    detect_node_project,
    select_node_manager,
)


@dataclass(frozen=True)
class InstallEnvironment:
    """Available package managers, install context, and Node project policy.

    Attributes:
        install_context: How lintro was installed.
        available_managers: Set of package manager identifiers found on PATH.
        node_project: The enclosing Node.js project, when the command runs
            inside one. Drives package-manager selection and the local-versus-
            global decision for npm-installed tools (#2005).
        explicit_node_manager: Package manager the user named explicitly. Beats
            every form of project evidence.
        prefer_global: Whether the user asked for global installs. Global is
            otherwise reserved for environments with no project manifest.
    """

    install_context: InstallContext
    available_managers: frozenset[PackageManager]
    node_project: NodeProject | None = None
    explicit_node_manager: PackageManager | None = None
    prefer_global: bool = False

    def node_manager(self) -> tuple[PackageManager, NodeManagerSource]:
        """Resolve which Node.js package manager to use, and why.

        Returns:
            Tuple of the chosen manager and the evidence that chose it.
        """
        return select_node_manager(
            available=self.available_managers,
            project=self.node_project,
            explicit=self.explicit_node_manager,
        )

    def installs_globally(self) -> bool:
        """Report whether npm-installed tools should go to a global prefix.

        A project manifest means the project owns its tool versions, so lintro
        adds a dev dependency instead of mutating a machine-wide prefix. Global
        is reserved for an explicit request or for an environment with no
        manifest at all (a bare CI runner, a Docker image, ``$HOME``).

        Returns:
            True when installs should be global.
        """
        return self.prefer_global or self.node_project is None

    def has(self, manager: PackageManager) -> bool:
        """Check if a package manager is available.

        Args:
            manager: Package manager to check.

        Returns:
            True if the manager was found on PATH.
        """
        return manager in self.available_managers

    @classmethod
    def detect(
        cls,
        install_context: InstallContext,
        *,
        explicit_node_manager: PackageManager | None = None,
        prefer_global: bool = False,
        start: Path | None = None,
    ) -> InstallEnvironment:
        """Detect available package managers and the enclosing Node project.

        Args:
            install_context: How lintro was installed (passed in from
                the existing ``_detect_install_context`` helper).
            explicit_node_manager: Package manager the user named explicitly.
            prefer_global: Whether the user asked for global installs.
            start: Directory to search for a ``package.json`` from. Defaults to
                the process working directory.

        Returns:
            InstallEnvironment with detected values.
        """
        managers: set[PackageManager] = set()
        for pm in PackageManager:
            if pm == PackageManager.PIP:
                # Accept pip3 as pip fallback
                if shutil.which("pip") is not None or shutil.which("pip3") is not None:
                    managers.add(pm)
            elif shutil.which(pm) is not None:
                managers.add(pm)
        return cls(
            install_context=install_context,
            available_managers=frozenset(managers),
            node_project=detect_node_project(start),
            explicit_node_manager=explicit_node_manager,
            prefer_global=prefer_global,
        )
