"""Continuous linting (watch mode) for Lintro.

This package implements ``lintro watch``: a filesystem watcher that
re-runs relevant tools on files as they change, with debouncing so a
burst of edits triggers a single run.

Modules:
    debouncer: Coalesce rapid change events into a single batched run.
    tool_selection: Map changed files to the tools relevant to them.
    runner: Execute the selected tools on a batch of changed files.
    watcher: Wire watchdog filesystem events into the debouncer + runner.
"""

from lintro.watch.debouncer import Debouncer
from lintro.watch.runner import WatchRunner
from lintro.watch.tool_selection import get_tools_for_file, select_tools_for_files
from lintro.watch.watcher import DEFAULT_IGNORE_PATTERNS, watch_paths

__all__ = [
    "DEFAULT_IGNORE_PATTERNS",
    "Debouncer",
    "WatchRunner",
    "get_tools_for_file",
    "select_tools_for_files",
    "watch_paths",
]
