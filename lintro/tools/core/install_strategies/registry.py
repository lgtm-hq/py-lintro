"""Strategy registry mapping install_type strings to strategy instances."""

from __future__ import annotations

from lintro.tools.core.install_strategies.base import InstallStrategy

_STRATEGIES: dict[str, InstallStrategy] = {}


def register_strategy(strategy: InstallStrategy) -> InstallStrategy:
    """Register a strategy for its install_type.

    Args:
        strategy: The strategy instance to register.

    Returns:
        The same strategy instance (allows use as a decorator).

    Raises:
        ValueError: If a strategy is already registered for the same install_type.
    """
    key = strategy.install_type()
    if key in _STRATEGIES:
        msg = f"Duplicate install strategy registration for {key!r}"
        raise ValueError(msg)
    _STRATEGIES[key] = strategy
    return strategy


def get_strategy(install_type: str) -> InstallStrategy | None:
    """Look up the strategy for an install_type.

    Args:
        install_type: The install_type string (e.g., ``"pip"``).

    Returns:
        The strategy instance, or None if unregistered.
    """
    return _STRATEGIES.get(install_type)


def strategy_registry() -> dict[str, InstallStrategy]:
    """Return a copy of the full registry.

    Returns:
        Dict mapping install_type to strategy.
    """
    return dict(_STRATEGIES)
