"""Execution configuration model."""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Supported artifact formats for side-channel output
ArtifactFormat = Literal["json", "csv", "markdown", "html", "sarif", "plain"]


def _get_default_max_workers() -> int:
    """Get default max workers based on CPU count.

    Returns:
        Number of CPUs available, clamped between 1 and 32.
    """
    cpu_count = os.cpu_count() or 4
    return max(1, min(cpu_count, 32))


class ExecutionConfig(BaseModel):
    """Execution control settings.

    Attributes:
        model_config: Pydantic model configuration.
        enabled_tools: List of tool names to run. If empty/None, all tools run.
        tool_order: Execution order strategy. One of:
            - "priority": Use default priority (formatters before linters)
            - "alphabetical": Alphabetical order
            - list[str]: Custom order as explicit list
        fail_fast: Stop on first tool failure.
        parallel: Run tools in parallel where possible.
        max_workers: Maximum number of parallel workers (default: CPU count).
        auto_install_deps: Auto-install Node.js dependencies if node_modules
            is missing. None means unset (falls back to container detection),
            True/False explicitly enables/disables.
        max_fix_retries: Maximum number of fix→verify cycles for converging
            formatters (default: 3). Some formatters need multiple passes.
        artifacts: Side-channel artifact formats to write alongside the
            primary output. Supports all output formats: json, csv,
            markdown, html, sarif, plain. When ``GITHUB_ACTIONS=true``
            is detected, SARIF is emitted automatically even if this
            list is empty.
        tool_snapshot_ttl: TTL in seconds for cached tool capability
            snapshots (default: 600). Binary path+mtime changes invalidate
            immediately regardless of TTL.
        strict_missing_tools: When True, unavailable tools fail the run
            (exit code 1). Default False — missing tools degrade visibly
            without counting as a lint failure.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    enabled_tools: list[str] = Field(default_factory=list)
    tool_order: str | list[str] = "priority"
    fail_fast: bool = False
    parallel: bool = True
    max_workers: int = Field(default_factory=_get_default_max_workers, ge=1, le=32)
    auto_install_deps: bool | None = None
    max_fix_retries: int = Field(default=3, ge=1, le=10)
    artifacts: list[ArtifactFormat] = Field(default_factory=list)
    tool_snapshot_ttl: int = Field(default=600, ge=1, le=86400)
    strict_missing_tools: bool = False
