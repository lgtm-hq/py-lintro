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
from lintro.models.core.severity_counts import SeverityCounts, SeverityDelta

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult


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
        severity_counts: Issue tallies by normalized severity for this run.
        previous_severity_counts: Severity tallies recorded for the previous
            run in this workspace, or ``None`` when none was recorded. The
            renderer reports the difference between the two.
        total_issues: Total issues found across all non-skipped tools.
        total_fixed: Total issues fixed (always 0 outside ``FIX`` mode).
        total_remaining: Issues still outstanding after the run.
        exit_code: Process exit code the run resolved to.
        dry_run_preview: Whether this was a ``fmt --dry-run`` preview.
        main_phase_empty_due_to_filter: Whether post-check filtering left the
            main phase with no tools to run. Carried here so a refreshed
            artifact resolves its exit code the same way the first pass did.
        early_exit: Whether the run stopped before executing any tool (bad
            tool selection, unresolvable ``--diff`` base, or a declined
            confirmation prompt). Renderers must emit nothing for such a run;
            the diagnostic has already been printed by the execute phase.
    """

    tool_results: list[ToolResult] = field(default_factory=list)
    action: Action = Action.CHECK
    workspace_root: Path = field(default_factory=Path.cwd)
    severity_counts: SeverityCounts = field(default_factory=SeverityCounts)
    previous_severity_counts: SeverityCounts | None = None
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
    def severity_delta(self) -> SeverityDelta | None:
        """Change in severity counts since the previous recorded run.

        Returns:
            SeverityDelta | None: Per-severity ``current - previous``
            differences, or ``None`` when no previous run was recorded and
            there is nothing to compare against.
        """
        if self.previous_severity_counts is None:
            return None
        return SeverityDelta.between(
            current=self.severity_counts,
            previous=self.previous_severity_counts,
        )
