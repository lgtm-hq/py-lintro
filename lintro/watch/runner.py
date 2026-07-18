"""Execute the selected tools on a batch of changed files.

The runner is intentionally thin: it decides which tools apply to the
changed files (smart selection), prints a compact timestamped header, and
delegates the actual execution to the shared
:func:`lintro.utils.tool_executor.run_lint_tools_simple` pipeline so watch
mode benefits from the same config injection, exclusions and formatting as
``lintro check``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from lintro.enums.action import Action
from lintro.utils.tool_executor import run_lint_tools_simple
from lintro.watch.tool_selection import select_tools_for_files

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["WatchRunner"]


@dataclass
class WatchRunner:
    """Run relevant tools on batches of changed files.

    Attributes:
        auto_fix: When True, run tools in fix mode instead of check mode.
        clear_screen: When True, clear the terminal before each run.
        output_format: Output format passed through to the executor.
        restrict_to: Optional user allowlist of tool names (``--tools``).
        exclude: Optional comma-separated exclude patterns.
        include_venv: Whether to include virtualenv directories.
        emit: Sink for status lines (defaults to ``print``); injectable for
            tests.
        run_tools: The execution backend; defaults to the shared
            ``run_lint_tools_simple`` and is injectable for tests.
    """

    auto_fix: bool = False
    clear_screen: bool = False
    output_format: str = "grid"
    restrict_to: list[str] | None = None
    exclude: str | None = None
    include_venv: bool = False
    emit: Callable[[str], None] = print
    run_tools: Callable[..., int] = run_lint_tools_simple

    _last_exit_code: int = field(default=0, init=False)

    @property
    def last_exit_code(self) -> int:
        """Return the exit code from the most recent run.

        Returns:
            The exit code of the last executed batch (0 if none ran).
        """
        return self._last_exit_code

    def run_batch(self, paths: set[str]) -> int:
        """Run the relevant tools for a batch of changed files.

        Args:
            paths: Set of changed file paths from the debouncer.

        Returns:
            Aggregated exit code from the tool run, or 0 when there is
            nothing to do (no existing files or no matching tools).
        """
        existing = sorted(p for p in paths if os.path.isfile(p))
        if not existing:
            return 0

        selected = select_tools_for_files(existing, restrict_to=self.restrict_to)

        if self.clear_screen:
            self._clear_screen()

        self._print_header(existing)

        if not selected:
            self.emit("  (no matching tools for changed files)")
            self._last_exit_code = 0
            return 0

        action = Action.FIX if self.auto_fix else Action.CHECK
        exit_code = self.run_tools(
            action=action,
            paths=existing,
            tools=",".join(selected),
            tool_options=None,
            exclude=self.exclude,
            include_venv=self.include_venv,
            group_by="file",
            output_format=self.output_format,
            verbose=False,
        )
        self._last_exit_code = int(exit_code)
        return self._last_exit_code

    def _print_header(self, paths: list[str]) -> None:
        """Print a timestamped header describing the changed files.

        Args:
            paths: Sorted list of changed file paths.
        """
        stamp = datetime.now().strftime("%H:%M:%S")
        rel = [os.path.relpath(p) for p in paths]
        shown = ", ".join(rel[:3])
        if len(rel) > 3:
            shown += f", (+{len(rel) - 3} more)"
        self.emit(f"[{stamp}] changed: {shown}")

    def _clear_screen(self) -> None:
        """Clear the terminal screen."""
        # ANSI clear + cursor home; avoids spawning a subprocess.
        self.emit("\033[2J\033[H")
