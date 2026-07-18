"""Configuration constants for Lintro.

This module provides constants, enums, and dataclasses used across the
unified configuration system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Any

from lintro.enums.tool_name import ToolName


class ToolOrderStrategy(StrEnum):
    """Strategy for ordering tool execution.

    Attributes:
        PRIORITY: Use tool priority values (formatters before linters).
        ALPHABETICAL: Alphabetical by tool name.
        CUSTOM: Custom order defined in config.
    """

    PRIORITY = auto()
    ALPHABETICAL = auto()
    CUSTOM = auto()


@dataclass
class ToolConfigInfo:
    """Information about a tool's configuration sources.

    Attributes:
        tool_name: Name of the tool.
        native_config: Configuration from native tool config files.
        lintro_tool_config: Configuration from [tool.lintro.<tool>].
        effective_config: Computed effective configuration.
        warnings: List of warnings about configuration issues.
        is_injectable: Whether Lintro can inject config to this tool.
    """

    tool_name: str
    native_config: dict[str, Any] = field(default_factory=dict)
    lintro_tool_config: dict[str, Any] = field(default_factory=dict)
    effective_config: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    is_injectable: bool = True


# Global settings that Lintro can manage across tools.
# Each setting maps to tool-specific config keys and indicates which tools
# support injection via Lintro config (vs requiring native config files).
GLOBAL_SETTINGS: dict[str, dict[str, Any]] = {
    "line_length": {
        "tools": {
            ToolName.RUFF: "line-length",
            ToolName.BLACK: "line-length",
            ToolName.MARKDOWNLINT: "config.MD013.line_length",
            ToolName.YAMLLINT: "rules.line-length.max",
        },
        "injectable": {
            ToolName.RUFF,
            ToolName.BLACK,
            ToolName.MARKDOWNLINT,
            ToolName.YAMLLINT,
        },
    },
    "target_python": {
        "tools": {
            ToolName.RUFF: "target-version",
            ToolName.BLACK: "target-version",
        },
        "injectable": {ToolName.RUFF, ToolName.BLACK},
    },
    "indent_size": {
        "tools": {
            ToolName.RUFF: "indent-width",
        },
        "injectable": {ToolName.RUFF},
    },
    "quote_style": {
        "tools": {
            ToolName.RUFF: "quote-style",
        },
        "injectable": {ToolName.RUFF},
    },
}

# Default tool priorities (lower = runs first).
# Formatters run before linters to avoid false positives.
DEFAULT_TOOL_PRIORITIES: dict[str, int] = {
    ToolName.BLACK: 15,
    ToolName.RUFF: 20,
    ToolName.OXFMT: 25,
    ToolName.MARKDOWNLINT: 30,
    ToolName.YAMLLINT: 35,
    ToolName.BANDIT: 45,
    ToolName.HADOLINT: 50,
    ToolName.OXLINT: 50,
    ToolName.TERRAFORM: 50,
    ToolName.ACTIONLINT: 55,
    ToolName.MYPY: 82,
    ToolName.TSC: 82,
    ToolName.OSV_SCANNER: 90,
    ToolName.PYTEST: 100,
}
