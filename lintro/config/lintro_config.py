"""Main Lintro configuration model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lintro.config.enforce_config import EnforceConfig
from lintro.config.execution_config import ExecutionConfig
from lintro.config.score_config import ScoreConfig
from lintro.config.tool_config import LintroToolConfig

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.config.review_config import ReviewConfig

__all__ = [
    "AIConfig",  # noqa: F822 - resolved via module __getattr__
    "EnforceConfig",
    "ExecutionConfig",
    "LintroConfig",
    "LintroToolConfig",
    "ReviewConfig",  # noqa: F822 - resolved via module __getattr__
    "ScoreConfig",
]


def _default_ai_config() -> AIConfig:
    """Build a default AIConfig without importing AI at module load.

    Returns:
        Default AI configuration instance.
    """
    from lintro.ai.config import AIConfig

    return AIConfig()


def _default_review_config() -> ReviewConfig:
    """Build a default ReviewConfig without importing review at module load.

    Returns:
        Default review configuration instance.
    """
    from lintro.config.review_config import ReviewConfig

    return ReviewConfig()


def _coerce_ai_config(value: Any) -> Any:
    """Coerce raw AI config mappings into AIConfig.

    Args:
        value: Raw field value from construction or validation.

    Returns:
        An AIConfig instance (or the original value when already typed).
    """
    from lintro.ai.config import AIConfig

    if isinstance(value, AIConfig):
        return value
    if value is None:
        return AIConfig()
    if isinstance(value, dict):
        return AIConfig(**value)
    return value


def _coerce_review_config(value: Any) -> Any:
    """Coerce raw review config mappings into ReviewConfig.

    Args:
        value: Raw field value from construction or validation.

    Returns:
        A ReviewConfig instance (or the original value when already typed).
    """
    from lintro.config.review_config import ReviewConfig

    if isinstance(value, ReviewConfig):
        return value
    if value is None:
        return ReviewConfig()
    if isinstance(value, dict):
        return ReviewConfig(**value)
    return value


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
        config_path: Path to the config file (set by loader).
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    enforce: EnforceConfig = Field(default_factory=EnforceConfig)
    defaults: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tools: dict[str, LintroToolConfig] = Field(default_factory=dict)
    # Lazy factories + before-validators keep cold imports free of AI/review
    # packages while still coercing dict inputs into the nested models.
    ai: Any = Field(default_factory=_default_ai_config)
    review: Any = Field(default_factory=_default_review_config)
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    config_path: str | None = None

    @field_validator("ai", mode="before")
    @classmethod
    def _validate_ai_config(cls, value: Any) -> Any:
        """Coerce AI config values before assignment.

        Args:
            value: Raw AI field value.

        Returns:
            Coerced AIConfig instance.
        """
        return _coerce_ai_config(value)

    @field_validator("review", mode="before")
    @classmethod
    def _validate_review_config(cls, value: Any) -> Any:
        """Coerce review config values before assignment.

        Args:
            value: Raw review field value.

        Returns:
            Coerced ReviewConfig instance.
        """
        return _coerce_review_config(value)

    def get_tool_config(self, tool_name: str) -> LintroToolConfig:
        """Get configuration for a specific tool.

        Args:
            tool_name: Name of the tool (e.g., "ruff", "black").

        Returns:
            LintroToolConfig: Tool configuration. Returns default config if not
                explicitly configured.
        """
        return self.tools.get(tool_name.lower(), LintroToolConfig())

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
        tool_lower = tool_name.lower()

        # Check execution.enabled_tools filter
        if self.execution.enabled_tools:
            enabled_lower = [t.lower() for t in self.execution.enabled_tools]
            if tool_lower not in enabled_lower:
                return False

        # Check tool-specific enabled flag
        tool_config = self.get_tool_config(tool_lower)
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


def __getattr__(name: str) -> Any:
    """Lazily re-export AIConfig and ReviewConfig for public API compatibility.

    Args:
        name: Attribute name being accessed.

    Returns:
        The requested config class.

    Raises:
        AttributeError: If ``name`` is not a deferred re-export.
    """
    if name == "AIConfig":
        from lintro.ai.config import AIConfig

        return AIConfig
    if name == "ReviewConfig":
        from lintro.config.review_config import ReviewConfig

        return ReviewConfig
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
