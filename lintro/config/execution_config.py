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
        enabled_tools: Strict allowlist of tool names to run on default
            (no ``--tools``) and ``--tools all`` runs. ``None`` (key absent)
            keeps the default selection: all tools when a config file is
            present, or a language-scoped set on a no-config first run (use
            ``--tools all`` or ``lintro init`` for the full set). A non-empty
            list restricts the run to exactly those tools; tools outside it
            are not reported individually. An explicit empty list is deprecated:
            the loader treats it as unset and the validator warns. An explicit comma-separated ``--tools`` list
            bypasses this allowlist.
        fail_fast: Stop on first tool failure.
        parallel: Run tools in parallel where possible.
        max_workers: Maximum number of parallel workers (default: CPU count).
        auto_install_deps: Auto-install Node.js dependencies if node_modules
            is missing. None means unset (falls back to container detection),
            True/False explicitly enables/disables.
        max_fix_retries: Maximum number of fix→verify cycles for converging
            formatters (default: 3). Some formatters need multiple passes.
        precedence: Manual write-precedence overrides for tools that can
            rewrite the same file, as ``[winner, loser]`` pairs. The winner
            has authority on every scope the two share: it runs **last**, so
            its write is the one that survives, and it keeps ``FORMAT`` while
            the loser's ``FORMAT`` is demoted (its ``CHECK`` still runs).
            Pairs that contradict each other fail planning rather than
            falling back silently.
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
        version_check_timeout: Seconds one tool version probe may run
            (default: 30, minimum 1). A probe that times out is retried once
            with double the budget, capped at 120 seconds. The
            ``LINTRO_VERSION_TIMEOUT`` environment variable takes precedence.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    enabled_tools: list[str] | None = None
    fail_fast: bool = False
    parallel: bool = True
    max_workers: int = Field(default_factory=_get_default_max_workers, ge=1, le=32)
    auto_install_deps: bool | None = None
    max_fix_retries: int = Field(default=3, ge=1, le=10)
    precedence: list[tuple[str, str]] = Field(default_factory=list)
    artifacts: list[ArtifactFormat] = Field(default_factory=list)
    tool_snapshot_ttl: int = Field(default=600, ge=1, le=86400)
    strict_missing_tools: bool = False
    version_check_timeout: int = Field(default=30, ge=1)
