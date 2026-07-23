"""Main Lintro configuration model."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lintro.ai.config import AIConfig
from lintro.config.enforce_config import EnforceConfig
from lintro.config.execution_config import ExecutionConfig
from lintro.config.output_config import OutputConfig
from lintro.config.review_config import ReviewConfig
from lintro.config.score_config import ScoreConfig
from lintro.config.tool_config import LintroToolConfig

__all__ = [
    "AIConfig",
    "EnforceConfig",
    "ExecutionConfig",
    "LintroConfig",
    "LintroToolConfig",
    "OutputConfig",
    "ReviewConfig",
    "ScoreConfig",
]


def _tool_name_aliases(tool_name: str) -> tuple[str, ...]:
    """Return case-normalized lookup aliases for hyphen/underscore tool names."""
    tool_lower = tool_name.lower()
    candidates = [
        tool_lower,
        tool_lower.replace("-", "_"),
        tool_lower.replace("_", "-"),
    ]
    return tuple(dict.fromkeys(candidates))


def _contains_tool_name_alias(tool_name: str, configured_names: list[str]) -> bool:
    """Check whether a configured tool-name list contains any spelling alias."""
    aliases = set(_tool_name_aliases(tool_name))
    configured = {name.lower() for name in configured_names}
    return not aliases.isdisjoint(configured)


class LintroConfig(BaseModel):
    """Main Lintro configuration container.

    This is the root configuration object loaded from .lintro-config.yaml.
    Follows the tiered model:

    1. execution: What tools run and how
    2. enforce: Cross-cutting settings that override native configs
    3. defaults: Fallback config when no native config exists
    4. tools: Per-tool enable/disable and config source
    5. ai: Optional AI-powered issue intelligence

    Attributes:
        model_config: Pydantic model configuration.
        execution: Execution control settings.
        enforce: Cross-cutting settings enforced via CLI flags.
        defaults: Fallback configs for tools without native configs.
        tools: Per-tool configuration, keyed by tool name.
        ai: AI-powered features configuration (optional, disabled by default).
        review: Diff review command configuration (checklist items).
        score: Health score weights and scale (0-100 metric).
        output: Console output presentation settings (e.g. ASCII art toggle).
        config_path: Path to the config file (set by loader).
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    enforce: EnforceConfig = Field(default_factory=EnforceConfig)
    defaults: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tools: dict[str, LintroToolConfig] = Field(default_factory=dict)
    ai: AIConfig = Field(default_factory=AIConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    config_path: str | None = None

    def get_tool_config(self, tool_name: str) -> LintroToolConfig:
        """Get configuration for a specific tool.

        Args:
            tool_name: Name of the tool (e.g., "ruff", "black").

        Returns:
            LintroToolConfig: Tool configuration. Returns default config if not
                explicitly configured.
        """
        tool_configs = {name.lower(): config for name, config in self.tools.items()}
        for candidate in _tool_name_aliases(tool_name):
            if candidate in tool_configs:
                return tool_configs[candidate]
        return LintroToolConfig()

    def is_tool_in_enabled_tools(self, tool_name: str) -> bool:
        """Check if a tool is allowed by ``execution.enabled_tools``.

        Args:
            tool_name: Name of the tool.

        Returns:
            bool: True when ``enabled_tools`` is empty or contains the tool name
                using either hyphen or underscore spelling.
        """
        if not self.execution.enabled_tools:
            return True
        return _contains_tool_name_alias(tool_name, self.execution.enabled_tools)

    def is_tool_enabled(self, tool_name: str) -> bool:
        """Check if a tool is enabled.

        A tool is enabled if:
        1. execution.enabled_tools is empty (all tools enabled), OR
        2. tool_name is in execution.enabled_tools, AND
        3. The tool's config has enabled=True (default)

        Args:
            tool_name: Name of the tool.

        Returns:
            bool: True if tool should run.
        """
        # Check execution.enabled_tools filter
        if not self.is_tool_in_enabled_tools(tool_name):
            return False

        # Check tool-specific enabled flag
        tool_config = self.get_tool_config(tool_name)
        return bool(tool_config.enabled)

    def get_tool_defaults(self, tool_name: str) -> dict[str, Any]:
        """Get default configuration for a tool.

        Used when the tool has no native config file.

        Args:
            tool_name: Name of the tool.

        Returns:
            dict[str, Any]: Default configuration or empty dict.
        """
        return self.defaults.get(tool_name.lower(), {})

    def get_effective_line_length(self, tool_name: str) -> int | None:
        """Get effective line length for a specific tool.

        In the tiered model, this simply returns the enforce.line_length
        value, which will be injected via CLI flags.

        Args:
            tool_name: Name of the tool (unused, kept for compatibility).

        Returns:
            int | None: Enforced line length or None.
        """
        line_length: int | None = self.enforce.line_length
        return line_length

    def get_effective_target_python(self, tool_name: str) -> str | None:
        """Get effective Python target version for a specific tool.

        In the tiered model, this simply returns the enforce.target_python
        value, which will be injected via CLI flags.

        Args:
            tool_name: Name of the tool (unused, kept for compatibility).

        Returns:
            str | None: Enforced target version or None.
        """
        target_python: str | None = self.enforce.target_python
        return target_python
