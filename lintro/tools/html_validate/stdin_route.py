"""Validate glob-named files through html-validate's stdin (#3396).

html-validate expands every positional argument as a glob, so a file named
``[slug].html`` or ``a{1,2}.html`` does not match itself and is never
validated. ``--stdin`` validates the file's content instead, one file per
call, run from the file's own directory so the same ``.htmlvalidate.json``
lookup applies.

The file's name is never put on the command line, not even as
``--stdin-filename``: the ``npx`` fallback runs html-validate through
``/bin/sh``, and npm's quoting leaves ``[``, ``]``, ``{``, ``}`` and ``,``
bare, so the shell rewrites ``[slug].html`` to a neighbouring ``s.html``.
html-validate reports the source as ``/dev/stdin``, and lintro puts the real
path on that report itself (:func:`rename_stdin_report`).

Stdin differs from a positional run in ways lintro cannot emulate reliably:
flat configs and transforms match ``/dev/stdin``, ``.htmlvalidateignore`` is
skipped, the working directory resolves symlinks, and JavaScript configs or
presets can do anything. So a file goes to stdin only when a strict
allowlist proves none of that can matter (checked against html-validate
11.21.0):

- no ``html-validate.config.*`` flat config above the file or the run's
  working directory;
- no symlink in the file's path or the working directory;
- every ``.htmlvalidate.*`` config above the file is JSON, using only the
  keys in :data:`_ALLOWED_CONFIG_KEYS` and only built-in presets;
- any ``.htmlvalidateignore`` above the file is ASCII, as is the path, and
  none of its patterns matches, case-insensitively with negations dropped.

Anything else, including a file that cannot be read, is held back as before
#3396: the run fails and names the file as not inspected. Rules see the
source's filename as ``/dev/stdin``; only the report carries the real path.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pathspec

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.tools.core.incomplete_run import incomplete_run_result

HTML_VALIDATE_IGNORE_FILENAME: str = ".htmlvalidateignore"

# The flat config files html-validate 11's CLI looks for from its working
# directory upward before any ``.htmlvalidate.*`` file
# (``FLAT_CONFIG_FILENAMES`` in ``dist/esm/core-nodejs.js``).
_FLAT_CONFIG_FILENAMES: tuple[str, ...] = (
    "html-validate.config.mjs",
    "html-validate.config.cjs",
    "html-validate.config.js",
    "html-validate.config.mts",
    "html-validate.config.cts",
    "html-validate.config.ts",
)

# The legacy config files it searches per directory
# (``findConfigurationFiles`` in ``dist/esm/core-nodejs.js``). Only the JSON
# one can be read without running code.
_JSON_CONFIG_FILENAME: str = ".htmlvalidate.json"
_SCRIPT_CONFIG_FILENAMES: tuple[str, ...] = (
    ".htmlvalidate.mjs",
    ".htmlvalidate.cjs",
    ".htmlvalidate.js",
)

# Config keys that cannot bring in a transform, plugin or code. ``elements``
# is not one: an entry can be a JavaScript module, and a JSON entry resolves
# ``<rootDir>`` from the working directory, which differs on the stdin route.
_ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset(
    {"$schema", "root", "extends", "rules"},
)

# html-validate's built-in presets: the ``presets`` object in
# ``dist/esm/core.js`` of html-validate 11.21.0. Any other ``extends`` entry
# (a package, a path, a ``html-validate:<file>`` spec) can run JavaScript.
_BUILTIN_PRESETS: frozenset[str] = frozenset(
    {
        "html-validate:a11y",
        "html-validate:browser",
        "html-validate:document",
        "html-validate:prettier",
        "html-validate:recommended",
        "html-validate:standard",
    },
)

FLAT_CONFIG_PRESENT: str = "flat config present"
SYMLINKED_PATH: str = "symlinked path"
CONFIG_NOT_ALLOWED: str = "config not on the allowlist"
IGNORE_NOT_INAPPLICABLE: str = "ignore file not provably inapplicable"


#: The name html-validate gives a source read from stdin in its report.
STDIN_FILENAME: str = "/dev/stdin"


def stdin_command(base: Sequence[str]) -> list[str]:
    """Build the argv that validates one file read from stdin.

    Args:
        base: The resolved executable plus ``--formatter json``.

    Returns:
        An argv that names no file at all, so no runner or shell can glob,
        split or rewrite the file's name.
    """
    return [*base, "--stdin"]


def rename_stdin_report(
    result: SubprocessResult,
    *,
    filename: str,
) -> SubprocessResult:
    """Put a file's real path on the report html-validate printed for stdin.

    Args:
        result: A finished ``--stdin`` call.
        filename: The path the content was read from.

    Returns:
        The result with every ``/dev/stdin`` report renamed, or unchanged
        when stdout is not a JSON report list (the caller then reports a
        malformed or missing report as usual).
    """
    try:
        reports = json.loads(result.stdout)
    except ValueError:
        return result
    if not isinstance(reports, list):
        return result
    for report in reports:
        if isinstance(report, dict) and report.get("filePath") == STDIN_FILENAME:
            report["filePath"] = filename
    stdout = json.dumps(reports)
    return replace(
        result,
        stdout=stdout,
        output=result.output.replace(result.stdout, stdout, 1),
    )


def _ancestors(directory: Path) -> list[Path]:
    """List a directory and each parent up to the filesystem root.

    Args:
        directory: Absolute directory to start from.

    Returns:
        The directory first, then each parent in turn.
    """
    return [directory, *directory.parents]


def _symlinked(path: Path) -> bool:
    """Report whether any component of an absolute path is a symlink.

    Args:
        path: An absolute, normalised path.

    Returns:
        True when resolving symlinks changes the path.
    """
    return os.path.realpath(path) != os.path.abspath(path)


def _flat_config(directory: Path) -> Path | None:
    """Find a flat config in a directory or above it.

    Args:
        directory: Absolute directory to start from.

    Returns:
        The first flat config found, or None.
    """
    for folder in _ancestors(directory):
        for name in _FLAT_CONFIG_FILENAMES:
            if (folder / name).exists():
                return folder / name
    return None


def _config_problem(config_file: Path) -> str | None:
    """Check one ``.htmlvalidate.json`` against the allowlist.

    Args:
        config_file: An existing JSON config.

    Returns:
        What is not allowed, or None when the whole file is allowed.
    """
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return "cannot be read"
    if not isinstance(config, dict):
        return "is not an object"
    extra = sorted(set(config) - _ALLOWED_CONFIG_KEYS)
    if extra:
        return f"has key {extra[0]!r}"
    extends = config.get("extends", [])
    if not isinstance(extends, list) or not all(
        isinstance(entry, str) and entry in _BUILTIN_PRESETS for entry in extends
    ):
        return "extends something other than a built-in preset"
    return None


def _ignore_matches(ignore_file: Path, relative: str) -> bool:
    """Check whether an ignore file may ignore a path.

    Args:
        ignore_file: An existing ``.htmlvalidateignore``.
        relative: The path relative to the ignore file's directory.

    Returns:
        False only when the file and path are ASCII, the file parses, and
        no non-negated pattern matches case-insensitively.
    """
    try:
        lines = ignore_file.read_text(encoding="ascii").splitlines()
        if not relative.isascii():
            return True
        spec = pathspec.GitIgnoreSpec.from_lines(
            [line.lower() for line in lines if not line.lstrip().startswith("!")],
        )
        return bool(spec.match_file(relative.lower()))
    except Exception:  # any read, decode or parse error may hide a match
        return True


@dataclass
class StdinRoute:
    """Decide which glob-named files stdin can validate faithfully.

    Attributes:
        cwd: The run's working directory; relative paths resolve against it.
    """

    cwd: str
    _reasons: dict[str, str | None] = field(default_factory=dict, repr=False)
    _directories: dict[Path, str | None] = field(default_factory=dict, repr=False)

    def absolute(self, path: str) -> Path:
        """Resolve a passed path against the working directory.

        Args:
            path: A relative or absolute path as passed to the tool.

        Returns:
            The absolute path, with ``..`` segments resolved the way Node's
            ``path.join`` resolves them.
        """
        return Path(os.path.normpath(os.path.join(self.cwd, path)))

    def hold_back_reason(self, path: str) -> str | None:
        """Say which allowlist rule keeps a file off stdin.

        Args:
            path: A relative or absolute path as passed to the tool.

        Returns:
            None when the file may go to stdin; otherwise the failed rule,
            followed by the path that broke it.
        """
        if path not in self._reasons:
            self._reasons[path] = self._judge(self.absolute(path))
        return self._reasons[path]

    def _judge(self, target: Path) -> str | None:
        """Apply the allowlist to one file.

        Args:
            target: Absolute path of the file.

        Returns:
            The first failed rule, or None.
        """
        if not target.is_file() or not os.access(target, os.R_OK):
            return "the file cannot be read"
        if _symlinked(target):
            return f"{SYMLINKED_PATH}: {target}"
        directory_reason = self._judge_directory(target.parent)
        if directory_reason is not None:
            return directory_reason
        for folder in _ancestors(target.parent):
            ignore_file = folder / HTML_VALIDATE_IGNORE_FILENAME
            if ignore_file.exists() and _ignore_matches(
                ignore_file,
                os.path.relpath(target, folder),
            ):
                return f"{IGNORE_NOT_INAPPLICABLE}: {ignore_file}"
        return None

    def _judge_directory(self, directory: Path) -> str | None:
        """Apply the rules shared by every file in a directory, once.

        Args:
            directory: The file's directory.

        Returns:
            The first failed rule, or None.
        """
        if directory not in self._directories:
            self._directories[directory] = self._directory_reason(directory)
        return self._directories[directory]

    def _directory_reason(self, directory: Path) -> str | None:
        """Check symlinks, flat configs and legacy configs for a directory.

        Args:
            directory: The file's directory.

        Returns:
            The first failed rule, or None.
        """
        cwd = Path(os.path.abspath(self.cwd))
        for place in (directory, cwd):
            if _symlinked(place):
                return f"{SYMLINKED_PATH}: {place}"
            flat = _flat_config(place)
            if flat is not None:
                return f"{FLAT_CONFIG_PRESENT}: {flat}"
        for folder in _ancestors(directory):
            for name in _SCRIPT_CONFIG_FILENAMES:
                if (folder / name).exists():
                    return f"{CONFIG_NOT_ALLOWED}: {folder / name} is not JSON"
            config_file = folder / _JSON_CONFIG_FILENAME
            problem = _config_problem(config_file) if config_file.exists() else None
            if problem is not None:
                return f"{CONFIG_NOT_ALLOWED}: {config_file} {problem}"
        return None


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
