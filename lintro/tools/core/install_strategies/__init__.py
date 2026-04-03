"""Install strategy package — strategy-per-install-type for tool management.

Importing this package triggers registration of all built-in strategies.

Usage:
    from lintro.tools.core.install_strategies import get_strategy, InstallEnvironment

    env = InstallEnvironment.detect(install_context)
    strategy = get_strategy("pip")
    if strategy:
        cmd = strategy.install_hint(env, "ruff", "0.14.0", "ruff", None)
"""

# Import strategy modules to trigger registration at package import time.
from lintro.tools.core.install_strategies import (  # noqa: F401
    binary_strategy,
    cargo_strategy,
    npm_strategy,
    pip_strategy,
    rustup_strategy,
)
from lintro.tools.core.install_strategies.base import InstallStrategy
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.registry import (
    get_strategy,
    register_strategy,
    strategy_registry,
)

__all__ = [
    "InstallEnvironment",
    "InstallStrategy",
    "get_strategy",
    "register_strategy",
    "strategy_registry",
]
