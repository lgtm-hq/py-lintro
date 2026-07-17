"""Lintro configuration module.

This module provides a tiered configuration system:
1. EXECUTION: What tools run and how
2. ENFORCE: Cross-cutting settings injected via CLI flags
3. DEFAULTS: Fallback config when no native config exists
4. TOOLS: Per-tool enable/disable and config source

Key components:
- LintroConfig: Main configuration dataclass
- EnforceConfig: Cross-cutting settings enforced via CLI
- ConfigLoader: Loads .lintro-config.yaml
- ToolConfigGenerator: CLI injection and defaults generation

Exports are resolved lazily so importing a single config submodule does not
pull the AI/review config chain at cold start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.config.config_loader import clear_config_cache as clear_config_cache
    from lintro.config.config_loader import get_config as get_config
    from lintro.config.config_loader import get_default_config as get_default_config
    from lintro.config.config_loader import load_config as load_config
    from lintro.config.lintro_config import EnforceConfig as EnforceConfig
    from lintro.config.lintro_config import ExecutionConfig as ExecutionConfig
    from lintro.config.lintro_config import LintroConfig as LintroConfig
    from lintro.config.lintro_config import LintroToolConfig as LintroToolConfig
    from lintro.config.tool_config_generator import (
        generate_defaults_config as generate_defaults_config,
    )
    from lintro.config.tool_config_generator import (
        get_defaults_injection_args as get_defaults_injection_args,
    )
    from lintro.config.tool_config_generator import (
        get_enforce_cli_args as get_enforce_cli_args,
    )
    from lintro.config.tool_config_generator import (
        has_native_config as has_native_config,
    )

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "EnforceConfig": ("lintro.config.lintro_config", "EnforceConfig"),
    "ExecutionConfig": ("lintro.config.lintro_config", "ExecutionConfig"),
    "LintroConfig": ("lintro.config.lintro_config", "LintroConfig"),
    "LintroToolConfig": ("lintro.config.lintro_config", "LintroToolConfig"),
    "clear_config_cache": ("lintro.config.config_loader", "clear_config_cache"),
    "generate_defaults_config": (
        "lintro.config.tool_config_generator",
        "generate_defaults_config",
    ),
    "get_config": ("lintro.config.config_loader", "get_config"),
    "get_default_config": ("lintro.config.config_loader", "get_default_config"),
    "get_defaults_injection_args": (
        "lintro.config.tool_config_generator",
        "get_defaults_injection_args",
    ),
    "get_enforce_cli_args": (
        "lintro.config.tool_config_generator",
        "get_enforce_cli_args",
    ),
    "has_native_config": ("lintro.config.tool_config_generator", "has_native_config"),
    "load_config": ("lintro.config.config_loader", "load_config"),
}

__all__ = [
    "EnforceConfig",
    "ExecutionConfig",
    "LintroConfig",
    "LintroToolConfig",
    "clear_config_cache",
    "get_config",
    "get_default_config",
    "load_config",
    "get_enforce_cli_args",
    "has_native_config",
    "generate_defaults_config",
    "get_defaults_injection_args",
]


def __getattr__(name: str) -> Any:
    """Resolve public config exports on first access.

    Args:
        name: Attribute name being accessed.

    Returns:
        The lazily imported attribute.

    Raises:
        AttributeError: If ``name`` is not a public export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
