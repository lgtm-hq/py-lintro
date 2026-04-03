"""Install environment detection for strategy-based tool installation.

Provides a slim, frozen data object describing what package managers are
available on the current system.  Strategy classes receive this instead of
the full ``RuntimeContext`` so they stay decoupled from CI detection,
platform labels, and other unrelated concerns.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from lintro.enums.install_context import InstallContext, PackageManager


@dataclass(frozen=True)
class InstallEnvironment:
    """Available package managers and install context.

    Attributes:
        install_context: How lintro was installed.
        available_managers: Set of package manager identifiers found on PATH.
    """

    install_context: InstallContext
    available_managers: frozenset[PackageManager]

    def has(self, manager: PackageManager) -> bool:
        """Check if a package manager is available.

        Args:
            manager: Package manager to check.

        Returns:
            True if the manager was found on PATH.
        """
        return manager in self.available_managers

    @classmethod
    def detect(cls, install_context: InstallContext) -> InstallEnvironment:
        """Detect available package managers from PATH.

        Args:
            install_context: How lintro was installed (passed in from
                the existing ``_detect_install_context`` helper).

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
        )
