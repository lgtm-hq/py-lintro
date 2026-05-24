"""Runtime context detection for install-aware commands.

Detects how lintro was installed (Homebrew, pip, Docker, development),
what package managers are available, and whether the process is running
in CI.  Strategy-specific install/upgrade hints are now handled by
:mod:`lintro.tools.core.install_strategies`.

Usage:
    from lintro.tools.core.install_context import RuntimeContext

    ctx = RuntimeContext.detect()
    print(ctx.install_context)         # "pip"
    print(ctx.environment.has("uv"))   # True
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass

from lintro.enums.install_context import CISystem, InstallContext
from lintro.tools.core.install_strategies.environment import InstallEnvironment


@dataclass(frozen=True)
class RuntimeContext:
    """Detected runtime context for install-aware commands.

    Attributes:
        install_context: How lintro was installed.
        platform_label: Platform string (e.g., "macOS arm64", "Linux x86_64").
        environment: Detected package manager availability.
        is_ci: Whether running in a CI environment.
        ci_name: Name of the CI system if detected.
    """

    install_context: InstallContext
    platform_label: str
    environment: InstallEnvironment
    is_ci: bool
    ci_name: CISystem | None = None

    @classmethod
    def detect(cls) -> RuntimeContext:
        """Detect the current runtime context.

        Returns:
            RuntimeContext with detected values.
        """
        ctx = _detect_install_context()
        return cls(
            install_context=ctx,
            platform_label=_detect_platform_label(),
            environment=InstallEnvironment.detect(ctx),
            is_ci=_is_ci(),
            ci_name=CISystem.detect(),
        )


def _detect_install_context() -> InstallContext:
    """Detect how lintro was installed based on the executable path."""
    # Docker: check for Docker indicators
    if (
        os.path.exists("/.dockerenv")
        or os.environ.get("LINTRO_DOCKER") == "1"
        or os.environ.get("CONTAINER") == "docker"
    ):
        return InstallContext.DOCKER

    # Resolve symlinks so pip installs under /opt/homebrew/lib aren't
    # misclassified as Homebrew formula installs.
    exe_path = os.path.realpath(sys.executable)
    install_path = os.path.realpath(__file__)

    # Homebrew: resolved path under Cellar/ or linuxbrew (formula install).
    # Match lintro-full before lintro to avoid prefix collisions.
    cellar_formulas: tuple[tuple[str, InstallContext], ...] = (
        ("lintro-full", InstallContext.HOMEBREW_FULL),
        ("lintro", InstallContext.HOMEBREW_BIN),
    )
    for path in (install_path, exe_path):
        for formula, context in cellar_formulas:
            if f"/Cellar/{formula}/" in path:
                return context
        lower = path.lower()
        if "/homebrew/" not in lower:
            continue
        if "site-packages" in lower or "/lib/python" in lower:
            continue
        if "lintro-full" in lower:
            return InstallContext.HOMEBREW_FULL
        is_homebrew_bin = "/bin/" in lower or lower.endswith("/lintro")
        if "lintro" in lower and is_homebrew_bin:
            return InstallContext.HOMEBREW_BIN

    # Development: running from a git checkout
    # install_path is lintro/tools/core/install_context.py — 4 levels to repo root
    source_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(install_path))),
    )
    # Use os.path.exists (not isdir) to also detect .git files (worktrees/submodules)
    if os.path.exists(os.path.join(source_root, ".git")):
        return InstallContext.DEVELOPMENT

    # Default: pip/uv install
    return InstallContext.PIP


def _detect_platform_label() -> str:
    """Get a human-readable platform label."""
    system = platform.system()
    machine = platform.machine()

    os_names: dict[str, str] = {
        "Darwin": "macOS",
        "Linux": "Linux",
        "Windows": "Windows",
    }
    os_label = os_names.get(system, system)
    return f"{os_label} {machine}"


def _is_ci() -> bool:
    """Detect if running in a CI environment.

    Parses the generic ``CI`` env var as a boolean (``CI=false`` is not CI)
    and falls back to specific CI system detection via :class:`CISystem`.
    """
    ci_value = os.environ.get("CI", "").lower()
    if ci_value in ("1", "true", "yes", "on"):
        return True
    if ci_value in ("0", "false", "no", "off"):
        return False
    return CISystem.detect() is not None
