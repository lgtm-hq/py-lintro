"""Structured result of a completed lint/format/test run.

:class:`RunArtifact` is the value handed from the execute phase
(:func:`lintro.utils.tool_executor.execute_run`) to the render phase
(:func:`lintro.utils.execution.run_renderer.render_run`). It carries
everything a renderer, the public Python API, or the AI layer needs, so no
consumer has to re-parse Lintro's own output to learn what ran (issue #1823).

The model is deliberately core-only: it names no AI type and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from lintro.enums.action import Action

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.health_score import HealthScore


@dataclass
class RunArtifact:
    """Everything a completed execute phase produced.

    Attributes:
        tool_results: Results for every tool that ran, plus placeholder
            entries for tools that were skipped.
        action: The action that was executed (``CHECK``, ``FIX`` or ``TEST``).
            For a ``fmt --dry-run`` preview this is ``CHECK``, because the run
            executed in read-only check mode.
        workspace_root: Directory the run was invoked from.
        health: Deterministic 0-100 health score derived from the results.
        total_issues: Total issues found across all non-skipped tools.
        total_fixed: Total issues fixed (always 0 outside ``FIX`` mode).
        total_remaining: Issues still outstanding after the run.
        exit_code: Process exit code the run resolved to.
        dry_run_preview: Whether this was a ``fmt --dry-run`` preview.
        main_phase_empty_due_to_filter: Whether post-check filtering left the
            main phase with no tools to run. Carried here so a re-scored
            artifact resolves its exit code the same way the first pass did.
        early_exit: Whether the run stopped before executing any tool (bad
            tool selection, unresolvable ``--diff`` base, or a declined
            confirmation prompt). Renderers must emit nothing for such a run;
            the diagnostic has already been printed by the execute phase.
    """

    tool_results: list[ToolResult] = field(default_factory=list)
    action: Action = Action.CHECK
    workspace_root: Path = field(default_factory=Path.cwd)
    health: HealthScore | None = None
    total_issues: int = 0
    total_fixed: int = 0
    total_remaining: int = 0
    exit_code: int = 0
    dry_run_preview: bool = False
    main_phase_empty_due_to_filter: bool = False
    early_exit: bool = False

    @property
    def success(self) -> bool:
        """Whether the run finished without a failing exit code.

        Returns:
            bool: ``True`` when :attr:`exit_code` is zero.
        """
        return self.exit_code == 0

    @property
    def health_score(self) -> int:
        """Numeric health score for this run.

        Returns:
            int: The 0-100 health score, or 0 when the run never got far
            enough to be scored (see :attr:`early_exit`). Reported as 0 rather
            than 100 so an un-scored run is never mistaken for a perfect one.
        """
        return 0 if self.health is None else self.health.score
