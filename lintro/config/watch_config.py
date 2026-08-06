"""Watch-mode configuration model.

Defaults for ``lintro watch`` can be declared under a ``watch:`` section in
``.lintro-config.yaml`` (or ``[tool.lintro.watch]`` in ``pyproject.toml``):

.. code-block:: yaml

    watch:
      debounce_ms: 300
      auto_fix: false
      clear_screen: false
      tools: [ruff, mypy]
      ignore:
        - "**/__pycache__/**"
        - "**/.git/**"

CLI flags override these values at invocation time.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["WatchConfig"]

DEFAULT_DEBOUNCE_MS: int = 300


class WatchConfig(BaseModel):
    """Configuration defaults for ``lintro watch``.

    Attributes:
        model_config: Pydantic model configuration.
        debounce_ms: Quiet period, in milliseconds, before a batch of changes
            triggers a run.
        auto_fix: Run tools in fix mode instead of check mode.
        clear_screen: Clear the terminal between runs.
        tools: Optional allowlist of tool names to run (empty means smart
            selection over all applicable tools).
        ignore: Extra gitignore-style patterns to exclude from watching. The
            built-in defaults (VCS internals, caches, build artifacts,
            virtualenvs) always apply; these patterns extend them.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    debounce_ms: int = DEFAULT_DEBOUNCE_MS
    auto_fix: bool = False
    clear_screen: bool = False
    tools: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)

    @field_validator("debounce_ms")
    @classmethod
    def _validate_debounce(cls, value: int) -> int:
        """Ensure the debounce interval is non-negative.

        Args:
            value: Proposed debounce interval in milliseconds.

        Returns:
            The validated value.

        Raises:
            ValueError: If ``value`` is negative.
        """
        if value < 0:
            msg = f"watch.debounce_ms must be >= 0, got {value}"
            raise ValueError(msg)
        return value
