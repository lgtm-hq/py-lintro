"""Validate glob-named files through html-validate's stdin (#3396).

html-validate expands every positional argument as a glob, so a file named
``[slug].html`` or ``a{1,2}.html`` does not match itself and is never
validated. ``--stdin --stdin-filename <path>`` validates any name and reports
the findings under that name, one file per call.

The stdin path differs from a positional run in four ways, each checked
against html-validate 11.21.0:

- Configuration is looked up from the process working directory, not the
  file's directory. Each call therefore runs from the file's own directory,
  which gives the same lookup as a positional run.
- ``.htmlvalidateignore`` is never consulted. lintro applies it the way
  html-validate's ``IsIgnored`` does, and holds back a file a pattern ignores
  or whose ignore file cannot be read.
- Transformers are matched against ``/dev/stdin``, not the real name. A file
  a ``transform`` key matches, under either name, is held back, because stdin
  would validate the untransformed source or apply the wrong transformer.
- Rules see the source's filename as ``/dev/stdin``; only the report is
  renamed.

A file that cannot be read is held back too. Held-back files keep the
existing hold-back result: the run fails and names them as not inspected.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 - used safely with shell disabled
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.tools.core.incomplete_run import incomplete_run_result

HTML_VALIDATE_IGNORE_FILENAME: str = ".htmlvalidateignore"

#: The name html-validate gives the stdin source; transformers match it.
STDIN_FILENAME: str = "/dev/stdin"

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
    _reasons: dict[str, str | None] = field(default_factory=dict, repr=False)
    _transforms: dict[Path, list[str] | None] = field(
        default_factory=dict,
        repr=False,
    )
    _ignore_specs: dict[Path, pathspec.GitIgnoreSpec | None] = field(
        default_factory=dict,
        repr=False,
    )

    def absolute(self, path: str) -> Path:
        """Resolve a passed path against the working directory.

        Args:
            path: A relative or absolute path as passed to the tool.

        Returns:
            The absolute path.
        """
        return Path(self.cwd, path)

    def hold_back_reason(self, path: str) -> str | None:
        """Say why stdin cannot validate a file as a positional run would.

        Args:
            path: A relative or absolute path as passed to the tool.

        Returns:
            None when stdin is faithful for the file; otherwise the one
            reason that applied: the file is unreadable, an
            ``.htmlvalidateignore`` ignores it or cannot be read, or a
            transformer applies to it or cannot be checked.
        """
        if path not in self._reasons:
            self._reasons[path] = self._judge(self.absolute(path))
        return self._reasons[path]

    def _judge(self, target: Path) -> str | None:
        """Find the first reason stdin cannot validate a file.

        Args:
            target: Absolute path of the file.

        Returns:
            The reason, or None when stdin is faithful.
        """
        if not target.is_file() or not os.access(target, os.R_OK):
            return "the file cannot be read"
        return self._ignored(target) or self._transformed(target)

    def _ignored(self, target: Path) -> str | None:
        """Apply ``.htmlvalidateignore`` the way html-validate's CLI does.

        html-validate walks from the file's directory to the filesystem
        root and asks each ignore file, with gitignore semantics, about the
        path relative to that file's directory. Any match ignores the file.

        Args:
            target: Absolute path of the file.

        Returns:
            Why the file is held back, or None when no ignore file applies.
        """
        for folder in _ancestors(target.parent):
            ignore_file = folder / HTML_VALIDATE_IGNORE_FILENAME
            if not ignore_file.exists():
                continue
            spec = self._ignore_spec(ignore_file)
            if spec is None:
                return f"{ignore_file} cannot be read"
            if spec.match_file(os.path.relpath(target, folder)):
                return f"a pattern in {ignore_file} ignores it"
        return None

    def _ignore_spec(self, ignore_file: Path) -> pathspec.GitIgnoreSpec | None:
        """Parse an ignore file once.

        Args:
            ignore_file: Path of an existing ``.htmlvalidateignore``.

        Returns:
            Its patterns, or None when it cannot be read or parsed.
        """
        if ignore_file not in self._ignore_specs:
            try:
                lines = ignore_file.read_text(encoding="utf-8").splitlines()
                spec: pathspec.GitIgnoreSpec | None
                spec = pathspec.GitIgnoreSpec.from_lines(lines)
            except (OSError, UnicodeDecodeError, ValueError):
                spec = None
            self._ignore_specs[ignore_file] = spec
        return self._ignore_specs[ignore_file]

    def _transformed(self, target: Path) -> str | None:
        """Check the file against its configuration's transformer keys.

        Args:
            target: Absolute path of the file.

        Returns:
            Why the file is held back, or None when no transformer applies.
        """
        keys = self._transform_keys(target.parent)
        if keys is None:
            return "html-validate could not print the configuration for it"
        for key in keys:
            try:
                pattern = re.compile(key)
            except re.error:
                return f"its transform pattern {key!r} cannot be checked"
            if pattern.search(str(target)) or pattern.search(STDIN_FILENAME):
                return f"its transform pattern {key!r} applies to it"
        return None

    def _transform_keys(self, directory: Path) -> list[str] | None:
        """List the transformer patterns configured for a directory.

        The built-in preset declares none, so html-validate is only asked
        when a config file sits in the directory or above it.

        Args:
            directory: The directory a stdin call would run from.

        Returns:
            The ``transform`` keys, or None when the configuration could not
            be read.
        """
        if directory not in self._transforms:
            keys: list[str] | None = []
            if any(
                (folder / name).exists()
                for folder in _ancestors(directory)
                for name in _CONFIG_FILENAMES
            ):
                keys = self._print_config_transforms(directory)
            self._transforms[directory] = keys
        return self._transforms[directory]

    def _print_config_transforms(self, directory: Path) -> list[str] | None:
        """Ask html-validate for the stdin configuration of a directory.

        Args:
            directory: The directory a stdin call would run from.

        Returns:
            The ``transform`` keys, or None when html-validate printed no
            usable configuration.
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
        transform = config.get("transform") or {}
        if not isinstance(transform, dict):
            return None
        return [str(key) for key in transform]


def _sum_parse_failures(results: Sequence[ToolResult]) -> int | None:
    """Add the parse-failure counts that were recorded.

    Args:
        results: Results to add up.

    Returns:
        The sum, or None when no result recorded a count.
    """
    recorded = [r.parse_failures_count for r in results]
    counts = [count for count in recorded if count is not None]
    return sum(counts) if counts else None


def _joined_output(results: Sequence[ToolResult]) -> str | None:
    """Join the non-empty outputs of several results.

    Args:
        results: Results whose output to join.

    Returns:
        The outputs separated by newlines, or None when all were empty.
    """
    parts = [r.output for r in results if r.output and r.output.strip()]
    return "\n".join(part for part in parts if part) or None


def merge_results(
    results: Sequence[ToolResult],
    *,
    name: str,
    cwd: str | None,
) -> ToolResult:
    """Merge one batch result and per-file stdin results into one result.

    Args:
        results: At least one result, all from the same tool and run.
        name: Tool name for the merged result.
        cwd: Working directory for the merged result.

    Returns:
        The single result unchanged. Otherwise a "no files" part (every
        plain file ignored by html-validate) adds nothing; when every part
        is "no files" the first is returned. Any part that timed out or has
        no complete verdict makes an incomplete run that keeps every finding
        and every failed part's reason. Otherwise a checked verdict over
        every part.
    """
    if len(results) == 1:
        return results[0]
    ran = [result for result in results if not result.no_files]
    if not ran:
        return results[0]
    if len(ran) == 1:
        return ran[0]
    issues = [issue for result in ran for issue in result.issues]
    failures = _sum_parse_failures(ran)
    broken = [r for r in ran if r.execution_failed or r.timed_out]
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
