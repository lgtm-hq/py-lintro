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
- ``.htmlvalidateignore`` is never consulted. lintro checks it
  conservatively: every ignore file from the file's directory to the root,
  case-insensitively, with negation lines dropped, so a file html-validate
  might ignore is always held back. A parse error holds the file back too.
- Transformers are matched against ``/dev/stdin``, not the real name, and
  their patterns are JavaScript regular expressions, possibly with function
  values ``--print-config`` cannot show. The file is held back when a JSON
  config in the lookup chain has a ``transform`` key, a plugin, or a preset
  outside ``html-validate:``; when a JavaScript config is in the chain; when
  a config cannot be read; or when the resolved config has a transform or
  plugin.
- Rules see the source's filename as ``/dev/stdin``; only the report is
  renamed.

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

import pathspec

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.tools.core.incomplete_run import incomplete_run_result

HTML_VALIDATE_IGNORE_FILENAME: str = ".htmlvalidateignore"

# Config file names html-validate searches for. A JavaScript config can build
# transformers or the whole config at load time, which lintro cannot inspect.
_JSON_CONFIG_FILENAME: str = ".htmlvalidate.json"
_SCRIPT_CONFIG_FILENAMES: tuple[str, ...] = (
    ".htmlvalidate.mjs",
    ".htmlvalidate.cjs",
    ".htmlvalidate.js",
)

TRANSFORM_CONFIGURED: str = (
    "a transform is configured, which html-validate does not apply to stdin"
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
    _transforms: dict[Path, str | None] = field(
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
            The absolute path, with ``..`` segments resolved the way Node's
            ``path.join`` resolves them.
        """
        return Path(os.path.normpath(os.path.join(self.cwd, path)))

    def hold_back_reason(self, path: str) -> str | None:
        """Say why stdin cannot validate a file as a positional run would.

        Args:
            path: A relative or absolute path as passed to the tool.

        Returns:
            None when stdin is faithful for the file; otherwise the one
            reason that applied: the file is unreadable, an
            ``.htmlvalidateignore`` pattern may ignore it or cannot be
            parsed, or a transform is or may be configured.
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
        return self._ignored(target) or self._transform_reason(target.parent)

    def _ignored(self, target: Path) -> str | None:
        """Check ``.htmlvalidateignore`` files, erring towards holding back.

        html-validate walks from the file's directory to the filesystem
        root and asks each ignore file about the path relative to that
        file's directory, case-insensitively. lintro drops negation lines,
        so a negation can only add hold-backs, never remove one.

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
            unparsed = f"{ignore_file} cannot be read or parsed"
            if spec is None:
                return unparsed
            relative = os.path.relpath(target, folder).lower()
            try:
                matched = spec.match_file(relative)
            except Exception:  # any matcher error holds the file back
                return unparsed
            if matched:
                return f"a pattern in {ignore_file} may ignore it"
        return None

    def _ignore_spec(self, ignore_file: Path) -> pathspec.GitIgnoreSpec | None:
        """Parse an ignore file's non-negated patterns once, lower-cased.

        Args:
            ignore_file: Path of an existing ``.htmlvalidateignore``.

        Returns:
            Its patterns, or None when it cannot be read or parsed.
        """
        if ignore_file not in self._ignore_specs:
            spec: pathspec.GitIgnoreSpec | None
            try:
                lines = ignore_file.read_text(encoding="utf-8").splitlines()
                kept = [
                    line.lower() for line in lines if not line.lstrip().startswith("!")
                ]
                spec = pathspec.GitIgnoreSpec.from_lines(kept)
            except Exception:  # any read or parse error holds the file back
                spec = None
            self._ignore_specs[ignore_file] = spec
        return self._ignore_specs[ignore_file]

    def _transform_reason(self, directory: Path) -> str | None:
        """Say whether a transform is or may be configured for a directory.

        Args:
            directory: The directory a stdin call would run from.

        Returns:
            Why files there are held back, or None when no transform can
            apply.
        """
        if directory not in self._transforms:
            self._transforms[directory] = self._judge_transforms(directory)
        return self._transforms[directory]

    def _judge_transforms(self, directory: Path) -> str | None:
        """Find a configured or possible transform for a directory.

        Transform patterns are JavaScript regular expressions and their
        values may be functions, which ``--print-config`` drops, so no
        pattern is ever matched here. Any sign of a transform holds back.

        Args:
            directory: The directory a stdin call would run from.

        Returns:
            The reason, or None when only the built-in presets and JSON
            configs with no transform, plugin or external preset apply.
        """
        folders = _ancestors(directory)
        configs = [
            folder / _JSON_CONFIG_FILENAME
            for folder in folders
            if (folder / _JSON_CONFIG_FILENAME).exists()
        ]
        reason = _script_config_reason(folders) or next(
            (found for found in map(_json_config_reason, configs) if found),
            None,
        )
        if reason is not None or not configs:
            return reason
        config = self._print_config(directory)
        if config is None:
            return "html-validate could not print the configuration for it"
        if config.get("transform") or config.get("plugins"):
            return TRANSFORM_CONFIGURED
        return None

    def _print_config(self, directory: Path) -> dict[str, object] | None:
        """Ask html-validate for the stdin configuration of a directory.

        Args:
            directory: The directory a stdin call would run from.

        Returns:
            The resolved configuration, or None when html-validate printed
            no usable configuration.
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
        return config if isinstance(config, dict) else None


def _script_config_reason(folders: Sequence[Path]) -> str | None:
    """Find a JavaScript config in the lookup chain.

    Args:
        folders: The directory and its parents.

    Returns:
        The hold-back reason, or None when there is none.
    """
    for folder in folders:
        for name in _SCRIPT_CONFIG_FILENAMES:
            if (folder / name).exists():
                return (
                    f"a transform may be configured: {folder / name} is a "
                    "JavaScript config lintro cannot inspect, and "
                    "html-validate does not apply transforms to stdin"
                )
    return None


def _json_config_reason(config_file: Path) -> str | None:
    """Look for a transform, or a JavaScript source of one, in a JSON config.

    Plugins and presets outside ``html-validate:`` are JavaScript modules
    that can declare transforms ``--print-config`` cannot show.

    Args:
        config_file: An existing ``.htmlvalidate.json``.

    Returns:
        The hold-back reason, or None when the file declares none of them.
    """
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return f"{config_file} cannot be read"
    if not isinstance(config, dict):
        return f"{config_file} cannot be read"
    if "transform" in config:
        return f"{TRANSFORM_CONFIGURED} in {config_file}"
    extends = config.get("extends") or []
    external = not isinstance(extends, list) or any(
        not (isinstance(entry, str) and entry.startswith("html-validate:"))
        for entry in extends
    )
    if config.get("plugins") or external:
        return (
            f"a transform may be configured: {config_file} loads a plugin or "
            "an external preset lintro cannot inspect"
        )
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
