"""Tool configuration model."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lintro.rules import validate_rule_packs


class LintroToolConfig(BaseModel):
    """Configuration for a single tool.

    In the tiered model, tools use their native configs by default.
    Lintro only controls whether tools run and optionally specifies
    an explicit config source path.

    Attributes:
        model_config: Pydantic model configuration (class-level).
        enabled: Whether the tool is enabled.
        config_source: Optional explicit path to native config file.
            If not set, tool uses its own config discovery.
        auto_install: Per-tool auto-install override.
            None means inherit from global setting.
        rule_packs: Opt-in packaged rules for Semgrep; empty by default.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    enabled: bool = True
    config_source: str | None = None
    auto_install: bool | None = None
    rule_packs: list[str] = Field(default_factory=list)

    @field_validator("rule_packs", mode="before")
    @classmethod
    def _validate_rule_packs(cls, value: object) -> list[str]:
        """Reject invalid pack names before Pydantic can coerce option types.

        Args:
            value: Unvalidated pack selection.

        Returns:
            Validated pack names.
        """
        return validate_rule_packs(value)
