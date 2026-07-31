"""Shared per-run state threaded between the execute and render phases.

Splitting ``run_lint_tools_simple`` into an execute phase and a render phase
(issue #1823) means two functions need the same run-scoped objects: the
console logger whose buffer becomes ``console.log``, the
:class:`~lintro.utils.output.OutputManager` that owns the run directory, and a
handful of resolved output-mode flags.

:class:`RunContext` carries exactly those. It is built once by
:func:`lintro.utils.tool_executor.build_run_context` and passed to both
phases; the AI layer receives it too, so AI output lands in the same console
buffer as everything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.enums.action import Action


@dataclass(frozen=True)
class RunContext:
    """Run-scoped objects and output-mode flags shared by both phases.

    Attributes:
        action: The action actually executed. A ``fmt --dry-run`` preview
            reports ``CHECK`` here because it runs read-only.
        selection_action: The action used to *select* tools. It differs from
            :attr:`action` only for a dry-run preview, which selects the
            fixable tool set but executes in check mode.
        dry_run_preview: Whether this run is a ``fmt --dry-run`` preview.
        output_manager: Owner of the run directory and report files.
        logger: Console logger used for progress output and, via its buffer,
            for ``console.log``.
        lintro_config: The loaded Lintro configuration for this run.
        clean_stdout_output: Whether stdout must carry a single machine
            readable document (json/sarif/csv/markdown), which routes all
            decorative console UI to stderr.
        score_only: Whether stdout must carry only the numeric health score.
    """

    action: Action
    selection_action: Action
    dry_run_preview: bool
    output_manager: Any
    logger: Any
    lintro_config: Any
    clean_stdout_output: bool
    score_only: bool
