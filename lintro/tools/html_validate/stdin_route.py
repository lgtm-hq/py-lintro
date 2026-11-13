"""Validate glob-named files through html-validate's stdin (#3396).

html-validate expands every positional argument as a glob, so a file named
``[slug].html`` or ``a{1,2}.html`` does not match itself and is never
validated. ``--stdin --stdin-filename <path>`` validates any name and reports
the findings under that name, one file per call.

The stdin path differs from a positional run in three ways, each checked
against html-validate 11.21.0:

- Configuration is looked up from the process working directory, not the
  file's directory. Each call therefore runs from the file's own directory,
  which gives the same lookup as a positional run.
- ``.htmlvalidateignore`` is never consulted. A file below such an ignore
  file could be one html-validate would skip, so it is held back instead.
- Transformers are matched against ``/dev/stdin``, not the real name. A file
  whose resolved configuration declares any ``transform`` is held back,
  because stdin would validate the untransformed source.

A file that cannot be read is held back too. Held-back files keep the
existing hold-back result: the run fails and names them as not inspected.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - used safely with shell disabled
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.tools.core.incomplete_run import incomplete_run_result

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue

HTML_VALIDATE_IGNORE_FILENAME: str = ".htmlvalidateignore"

# Config file names html-validate searches for, in its own lookup order.
_CONFIG_FILENAMES: tuple[str, ...] = (
    ".htmlvalidate.json",
    ".htmlvalidate.mjs",
    ".htmlvalidate.cjs",
    ".htmlvalidate.js",
)

#: Runs an html-validate argv from a directory and returns its result.
RunTool = Callable[[list[str], str], SubprocessResult]


def stdin_command(base: Sequence[str], *, filename: str) -> list[str]:
    """Build the argv that validates one file read from stdin.

    Args:
        base: The resolved executable plus ``--formatter json``.
        filename: The real path findings are reported under.

    Returns:
        An argv that passes no positional path, so nothing is globbed.
    """
    return [*base, "--stdin", "--stdin-filename", filename]


def _ancestors(directory: Path) -> list[Path]:
    """List a directory and each parent up to the filesystem root.

    Args:
        directory: Absolute directory to start from.

    Returns:
        The directory first, then each parent in turn.
    """
    return [directory, *directory.parents]


@dataclass
class StdinRoute:
    """Decide which glob-named files stdin can validate faithfully.

    Attributes:
        base_command: The resolved executable plus ``--formatter json``.
        run_tool: Runs an argv from a directory; used for ``--print-config``.
        cwd: Directory the relative paths are resolved against.
    """

    base_command: list[str]
    run_tool: RunTool
    cwd: str
    _transforms: dict[Path, bool | None] = field(default_factory=dict, repr=False)

    def absolute(self, path: str) -> Path:
        """Resolve a passed path against the working directory.

        Args:
            path: A relative or absolute path as passed to the tool.

        Returns:
            The absolute path.
        """
        return Path(self.cwd, path)

    def can_validate(self, path: str) -> bool:
        """Report whether stdin validates a file as a positional run would.

        Args:
            path: A relative or absolute path as passed to the tool.

        Returns:
            True for a readable file with no ``.htmlvalidateignore`` above it
            and a resolved configuration that declares no transformer.
        """
        target = self.absolute(path)
        if not target.is_file() or not os.access(target, os.R_OK):
            return False
        directory = target.parent
        if any(
            (folder / HTML_VALIDATE_IGNORE_FILENAME).exists()
            for folder in _ancestors(directory)
        ):
            return False
        return self._declares_transform(directory) is False

    def _declares_transform(self, directory: Path) -> bool | None:
        """Report whether the configuration for a directory has transformers.

        The built-in preset declares none, so html-validate is only asked
        when a config file sits in the directory or above it.

        Args:
            directory: The directory a stdin call would run from.

        Returns:
            True or False, or None when the configuration could not be read.
        """
        if directory in self._transforms:
            return self._transforms[directory]
        answer: bool | None = False
        if any(
            (folder / name).exists()
            for folder in _ancestors(directory)
            for name in _CONFIG_FILENAMES
        ):
            answer = self._print_config_transform(directory)
        self._transforms[directory] = answer
        return answer

    def _print_config_transform(self, directory: Path) -> bool | None:
        """Ask html-validate for the stdin configuration of a directory.

        Args:
            directory: The directory a stdin call would run from.

        Returns:
            Whether the resolved configuration declares a transformer, or
            None when html-validate printed no configuration.
        """
        cmd = [*self.base_command, "--print-config", "--stdin"]
        try:
            result = self.run_tool(cmd, str(directory))
        except (OSError, subprocess.SubprocessError):
            return None
        if not result.success:
            return None
        try:
            config = json.loads(result.stdout)
        except ValueError:
            return None
        if not isinstance(config, dict):
            return None
        return bool(config.get("transform"))


def _sum_parse_failures(results: Sequence[ToolResult]) -> int | None:
    """Add the parse-failure counts that were recorded.

    Args:
        results: Results to add up.

    Returns:
        The sum, or None when no result recorded a count.
    """
    counts = [r.parse_failures_count for r in results if r.parse_failures_count]
    recorded = [r for r in results if r.parse_failures_count is not None]
    return sum(counts) if recorded else None


def _joined_output(results: Sequence[ToolResult]) -> str | None:
    """Join the non-empty outputs of several results.

    Args:
        results: Results whose output to join.

    Returns:
        The outputs separated by newlines, or None when all were empty.
    """
    parts = [r.output for r in results if (r.output or "").strip()]
    return "\n".join(part for part in parts if part) or None


def merge_results(
    results: Sequence[ToolResult],
    *,
    name: str,
    cwd: str | None,
    timed_out: Callable[[list[BaseIssue]], ToolResult],
) -> ToolResult:
    """Merge one batch result and per-file stdin results into one result.

    Args:
        results: At least one result, all from the same tool and run.
        name: Tool name for the merged result.
        cwd: Working directory for the merged result.
        timed_out: Builds a timeout result carrying the given findings.

    Returns:
        The single result unchanged; otherwise a timeout when any part
        timed out, an incomplete run when any part has no complete verdict,
        and a checked verdict over every part. A "no files" part (every
        plain file ignored by html-validate) adds nothing; when every part
        is "no files" the first is returned.
    """
    if len(results) == 1:
        return results[0]
    issues = [issue for result in results for issue in result.issues]
    if any(result.timed_out for result in results):
        return timed_out(issues)
    ran = [result for result in results if not result.no_files]
    if not ran:
        return results[0]
    if len(ran) == 1:
        return ran[0]
    failures = _sum_parse_failures(ran)
    broken = [result for result in ran if result.execution_failed]
    if broken:
        return incomplete_run_result(
            name=name,
            reason=_joined_output(broken) or "html-validate gave no verdict.",
            issues=issues,
            parse_failures=failures,
            cwd=cwd,
        )
    success = all(result.success for result in ran)
    return ToolResult.from_outcome(
        ToolOutcome(
            execution=Checked(
                success=success,
                inspected_files=(True if any(r.inspected_files for r in ran) else None),
            ),
            issues_count=len(issues),
        ),
        name=name,
        output=None if success else _joined_output(ran),
        issues=issues,
        cwd=cwd,
        parse_failures_count=failures,
    )
