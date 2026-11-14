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

The calls run in a bounded pool (:func:`run_stdin_calls`, #3433). Every
file, and so every directory's pin check, is judged before the first call
starts, and results are merged in input order, so the merged result does
not depend on which call finished first. Under the ``npx`` fallback the
calls run one at a time until one has run html-validate and parsed its
report, so the pool never installs a cold package concurrently.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Hashable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Generic, TypeVar

import pathspec

from lintro.config.config_loader import get_config
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.tools.core.incomplete_run import incomplete_run_result
from lintro.tools.core.node_fallback import (
    npx_pin_refusal,
    registry_fallback_spec,
    split_npm_spec,
)

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
PIN_NOT_VERIFIABLE: str = "pinned html-validate cannot be verified"


def _runner_refusal(command: Sequence[str], directory: Path) -> str | None:
    """Check that the command runs the same html-validate from a directory.

    The version gate verifies the command from the run's working directory
    only, and each stdin call runs from the file's own directory. Of the
    commands ``NodeJSBuilder`` resolves, a project-local or ``PATH`` binary
    is an absolute path and ``bunx <pkg>@<pin>`` resolves the pinned spec, so
    neither depends on the cwd. ``npx --package <spec> <bin>`` runs ``<bin>``
    from any ``node_modules/.bin`` above its cwd, so it is checked with the
    gate's own :func:`npx_pin_refusal`. A relative executable path would name
    a different file from another directory.

    Args:
        command: The resolved html-validate command.
        directory: The directory a stdin call would run from.

    Returns:
        Why the command cannot be trusted there, or None.
    """
    if not command:
        return None
    executable = command[0]
    if Path(executable).name == "npx":
        package, pin = split_npm_spec(registry_fallback_spec(command) or "")
        if pin is None:
            return "the npx command carries no version pin"
        return npx_pin_refusal(cwd=directory, package=package, pin=pin, command=command)
    if not os.path.isabs(executable) and ("/" in executable or os.sep in executable):
        return f"the executable path {executable!r} is relative"
    return None


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
        command: The resolved html-validate command, checked again from each
            stdin working directory when its resolution depends on the cwd.
    """

    cwd: str
    command: Sequence[str] = ()
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
        """Check symlinks, configs and the runner for a directory.

        Args:
            directory: The file's directory.

        Returns:
            The first failed rule, or None.
        """
        refusal = _runner_refusal(self.command, directory)
        if refusal is not None:
            return f"{PIN_NOT_VERIFIABLE} from {directory}: {refusal}"
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


#: The most stdin calls that run at once (#3433).
MAX_STDIN_WORKERS: int = 8

_Item = TypeVar("_Item")


def stdin_workers() -> int:
    """Size the stdin worker pool for this machine and configuration.

    Returns:
        ``min(os.cpu_count(), MAX_STDIN_WORKERS, execution.max_workers)``,
        and at least 1, so ``max_workers: 1`` runs the calls one at a time.
    """
    configured = get_config().execution.max_workers
    return max(1, min(os.cpu_count() or 1, MAX_STDIN_WORKERS, configured))


def finished_without_timeout(result: ToolResult) -> bool:
    """Accept any call that did not hit its deadline as a warm-up.

    Args:
        result: A finished stdin call.

    Returns:
        True unless the call timed out.
    """
    return not result.timed_out


def ran_html_validate(result: ToolResult) -> bool:
    """Accept only a call that ran html-validate and parsed its report.

    A file that could not be opened, a runner that could not start, and a
    call that printed no report or a malformed one are all execution
    failures, so none of them proves the runner's package is installed.

    Args:
        result: A finished stdin call.

    Returns:
        True for a call with a complete verdict.
    """
    return not (result.execution_failed or result.timed_out)


@dataclass
class _Schedule(Generic[_Item]):
    """The bookkeeping of one :func:`run_stdin_calls` run.

    Attributes:
        items: Every item, in input order.
        group: The warm-up group of an item.
        ready: Whether a warm-up call's result opens its group to the pool.
        stop: Set by a worker the moment its call times out.
        waiting: Indices not yet submitted, in input order.
        unstarted: Indices a worker picked up after ``stop`` was set.
        results: Results of the calls that ran, by index.
        open_groups: Groups whose items may share the pool.
        warming: Each group's warm-up call in flight, by future.
    """

    items: Sequence[_Item]
    group: Callable[[_Item], Hashable]
    ready: Callable[[ToolResult], bool]
    stop: threading.Event = field(default_factory=threading.Event)
    waiting: list[int] = field(default_factory=list)
    unstarted: list[int] = field(default_factory=list)
    results: dict[int, ToolResult] = field(default_factory=dict)
    open_groups: set[Hashable] = field(default_factory=set)
    warming: dict[Future[ToolResult | None], Hashable] = field(default_factory=dict)

    def next_batch(self, free: int) -> list[tuple[int, bool]]:
        """Pick the items to submit now, in input order.

        Args:
            free: How many calls may start.

        Returns:
            ``(index, is_warm_up)`` pairs: an item of an open group, or the
            first waiting item of a group with no warm-up in flight. Empty
            once ``stop`` is set.
        """
        batch: list[tuple[int, bool]] = []
        claimed = set(self.warming.values())
        for index in self.waiting:
            if len(batch) >= free or self.stop.is_set():
                break
            key = self.group(self.items[index])
            if key in self.open_groups:
                batch.append((index, False))
            elif key not in claimed:
                claimed.add(key)
                batch.append((index, True))
        if self.stop.is_set():
            return []
        for index, _ in batch:
            self.waiting.remove(index)
        return batch

    def start(
        self,
        index: int,
        call: Callable[[_Item], ToolResult],
    ) -> ToolResult | None:
        """Run one call on a worker unless a timeout came first.

        Args:
            index: The item to validate.
            call: Validates one item.

        Returns:
            The call's result, or None when ``stop`` was already set when
            the worker picked the item up; nothing is launched then.
        """
        if self.stop.is_set():
            return None
        result = call(self.items[index])
        if result.timed_out:
            self.stop.set()
        return result

    def finish(self, future: Future[ToolResult | None], index: int) -> None:
        """Record one finished call.

        Args:
            future: The call's future.
            index: Its item.
        """
        warm_up = future in self.warming
        key = self.warming.pop(future, None)
        result = future.result()
        if result is None:
            self.unstarted.append(index)
            return
        self.results[index] = result
        if warm_up and self.ready(result):
            self.open_groups.add(key)


def _one_group(item: object) -> Hashable:
    """Put every item in the same warm-up group.

    Args:
        item: Any item.

    Returns:
        The single group key.
    """
    del item
    return None


def run_stdin_calls(
    items: Sequence[_Item],
    call: Callable[[_Item], ToolResult],
    *,
    workers: int,
    group: Callable[[_Item], Hashable] = _one_group,
    ready: Callable[[ToolResult], bool] = finished_without_timeout,
) -> tuple[list[ToolResult], list[_Item]]:
    """Run one stdin call per item, at most ``workers`` at a time (#3433).

    Items warm up per ``group``: a group's items run one at a time, in
    input order, until one call's result is ``ready``; only then do the rest
    of that group share the pool. A cold registry runner therefore installs
    once per pinned spec rather than once per worker, and a first call that
    hangs costs one deadline.

    The first timeout sets a shared event from the worker that saw it. The
    main thread checks it before every submission, and each worker checks
    it before launching anything, so no call starts after it; calls already
    running finish and keep their results.

    Args:
        items: What to validate, in the order the run lists them.
        call: Validates one item; must not raise for a failed call.
        workers: The most calls in flight at once; below 1 counts as 1.
        group: The warm-up group of an item; one group by default.
        ready: Whether a warm-up call opens its group to the pool; by
            default any call that did not time out.

    Returns:
        The result of every call that ran, in input order whatever order
        they finished in, and the items never started, also in input order.
    """
    workers = max(1, workers)
    schedule = _Schedule(
        items=items,
        group=group,
        ready=ready,
        waiting=list(range(len(items))),
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        running: dict[Future[ToolResult | None], int] = {}
        while True:
            for index, warm_up in schedule.next_batch(workers - len(running)):
                future = pool.submit(schedule.start, index, call)
                running[future] = index
                if warm_up:
                    schedule.warming[future] = group(items[index])
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                schedule.finish(future, running.pop(future))
    never = sorted([*schedule.unstarted, *schedule.waiting])
    return (
        [schedule.results[index] for index in sorted(schedule.results)],
        [items[index] for index in never],
    )


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
