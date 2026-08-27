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
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lintro.enums.action import Action
from lintro.utils.tool_executor import run_lint_tools_simple
from lintro.watch.tool_selection import select_tools_for_files

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintro.models.core.tool_result import ToolResult

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
        watch_paths: Original file or directory roots, used for concise output.
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
    watch_paths: list[str] = field(default_factory=list)
    emit: Callable[[str], None] = print
    run_tools: Callable[..., int] = run_lint_tools_simple

    _last_exit_code: int = field(default=0, init=False)
    _event_kinds: dict[str, str] = field(default_factory=dict, init=False)
    _event_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

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
        with self._event_lock:
            event_kinds = {
                path: self._event_kinds.pop(path, "modified") for path in paths
            }
        existing = sorted(p for p in paths if os.path.isfile(p))
        if not existing:
            return 0

        selected = select_tools_for_files(
            existing,
            restrict_to=self.restrict_to,
            auto_fix=self.auto_fix,
        )

        if self.clear_screen:
            self._clear_screen()

        self._print_header(existing, event_kinds=event_kinds)

        if not selected:
            self.emit("  (no matching tools for changed files)")
            return 0

        action = Action.FIX if self.auto_fix else Action.CHECK
        try:
            execution_options: dict[str, object] = {
                "action": action,
                "paths": existing,
                "tools": ",".join(selected),
                "tool_options": None,
                "exclude": self.exclude,
                "include_venv": self.include_venv,
                "group_by": "file",
                "output_format": self.output_format,
                "verbose": False,
                "yes": True,
                "no_art": True,
                "run_post_checks": False,
                "on_tool_result": self._render_tool_result,
                "render_summary": False,
            }
            exit_code = self.run_tools(**execution_options)
        except Exception as exc:  # noqa: BLE001 - watch mode must survive a batch
            self.emit(f"  Error: {type(exc).__name__}: {exc}")
            self._last_exit_code = 1
            return 1
        self._last_exit_code = int(exit_code)
        return self._last_exit_code

    def record_event(self, path: str, kind: str) -> None:
        """Record the latest filesystem event kind for a changed path.

        Args:
            path: Changed file path.
            kind: Human-readable event kind such as ``created`` or ``modified``.
        """
        with self._event_lock:
            self._event_kinds[path] = kind

    def _print_header(
        self,
        paths: list[str],
        *,
        event_kinds: dict[str, str],
    ) -> None:
        """Print a timestamped header describing the changed files.

        Args:
            paths: Sorted list of changed file paths.
            event_kinds: Latest accepted event kind for each path.
        """
        stamp = datetime.now().strftime("%H:%M:%S")
        labels = [
            f"{self._display_path(path)} {event_kinds.get(path, 'modified')}"
            for path in paths
        ]
        shown = ", ".join(labels[:3])
        if len(labels) > 3:
            shown += f", (+{len(labels) - 3} more)"
        self.emit(f"[{stamp}] {shown}")

    def _render_tool_result(self, result: ToolResult) -> None:
        """Render one compact tool result for continuous output.

        Args:
            result: Completed tool result from the shared executor.
        """
        duration = (
            f" ({result.duration_seconds:.2f}s)"
            if result.duration_seconds is not None
            else ""
        )
        if result.skipped:
            status = f"⏭️ skipped: {result.skip_reason}"
        elif result.issues_count:
            noun = "issue" if result.issues_count == 1 else "issues"
            status = f"⚠️ {result.issues_count} {noun}"
        elif not result.success:
            status = "❌ failed"
        else:
            status = "✅ passed"
        self.emit(f"  ├─ {result.name}: {status}{duration}")

        for issue in result.issues or ():
            location = self._display_path(issue.file) if issue.file else ""
            if issue.line:
                location = (
                    f"{location}:{issue.line}" if location else f"line {issue.line}"
                )
            prefix = f"{location}: " if location else ""
            self.emit(f"  │  {prefix}{issue.message}")

    def _display_path(self, path: str) -> str:
        """Return a path relative to its configured watch root when possible.

        Args:
            path: Changed file path.

        Returns:
            Concise path for continuous output.
        """
        resolved = Path(path).resolve()
        for raw_root in self.watch_paths:
            root = Path(raw_root).resolve()
            base = root if root.is_dir() else root.parent
            if resolved.is_relative_to(base):
                return resolved.relative_to(base).as_posix()
        return os.path.relpath(path)

    def _clear_screen(self) -> None:
        """Clear the terminal screen."""
        # ANSI clear + cursor home; avoids spawning a subprocess.
        self.emit("\033[2J\033[H")
