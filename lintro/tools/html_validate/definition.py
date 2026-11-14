"""html-validate tool definition.

html-validate is an offline HTML validator that checks HTML documents against
best practices, standards, and accessibility (WCAG) rules. It is a Node.js tool
invoked via bun/bunx, mirroring lintro's other Node tools (markdownlint,
prettier).

Executable resolution prefers a consumer-local ``node_modules/.bin`` install,
then a binary on PATH, and only then a ``bunx``/``npx`` invocation pinned to the
version in ``package.json``; ``@latest`` is never resolved at runtime. Files are
always passed as literal paths so html-validate's glob expansion never runs. A
file whose name it would still read as a glob goes through ``--stdin`` instead
(see :mod:`lintro.tools.html_validate.stdin_route`).

The ``bunx``/``npx`` fallback is second-class: it needs registry access and it
imposes html-validate's own Node ``engines`` floor on the consumer. Selecting it
warns once, and a failure on that path is reported with install guidance naming
the pinned devDependency to add instead (see issue #1767).

No configuration is required: when no ``.htmlvalidate.*`` config is found,
html-validate applies its built-in ``html-validate:recommended`` preset, so the
tool produces sensible results out of the box.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 - used safely with shell disabled
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_outcome import Checked, NoFiles, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.html_validate.html_validate_parser import (
    parse_html_validate_output_with_stats,
)
from lintro.plugins.base import BaseToolPlugin, PreparedExecution
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.batch_runner import batch_timeout_result
from lintro.tools.core.incomplete_run import (
    incomplete_run_result,
    report_gap,
    tool_message_excerpt,
)
from lintro.tools.core.node_fallback import (
    is_registry_fallback_command,
    registry_fallback_guidance,
    registry_fallback_spec,
)
from lintro.tools.core.readable_paths import (
    run_on_readable_paths,
    unread_paths_reason,
)
from lintro.tools.html_validate.stdin_route import (
    StdinRoute,
    finished_without_timeout,
    merge_results,
    ran_html_validate,
    rename_stdin_report,
    run_stdin_calls,
    stdin_command,
    stdin_workers,
)

# Constants for html-validate configuration
HTML_VALIDATE_DEFAULT_TIMEOUT: int = 30
HTML_VALIDATE_FILE_PATTERNS: list[str] = ["*.html", "*.htm", "*.vue", "*.svelte"]
HTML_VALIDATE_CONFIG_FILENAMES: list[str] = [
    ".htmlvalidate.json",
    ".htmlvalidate.js",
    ".htmlvalidate.cjs",
    ".htmlvalidate.mjs",
]

# What html-validate prints, and exits 1 on, when its own ignore configuration
# (``.htmlvalidateignore``) excludes every file it was given (#3393).
HTML_VALIDATE_NO_FILES_MATCHING: str = "No files matching patterns"
HTML_VALIDATE_ALL_IGNORED_MESSAGE: str = (
    "No files to validate: html-validate's own ignore configuration "
    "(.htmlvalidateignore) excludes every file given; none was inspected."
)

# Characters html-validate's CLI treats as glob syntax. Passing any of them
# would push its ``expandFiles`` helper down the directory-walking branch of
# ``fs.globSync``, which is not implemented on every Bun release and aborts the
# run before any file is validated (see issue #1727).
_GLOB_METACHARACTERS: frozenset[str] = frozenset("*?[]{}")

# Shapes that can stop a literal path from matching itself in html-validate's
# ``fs.globSync`` expansion. Node builds its minimatch matcher with
# ``windowsPathsNoEscape: true`` (a backslash becomes a path separator),
# extglobs, brace expansion and character classes, but with ``nonegate`` and
# ``nocomment`` (``!`` and ``#`` stay literal). Such a path can be dropped or
# matched to a different file, so it is never passed to html-validate as an
# argument (#3393); it is validated through stdin instead (#3396).
# Each shape was checked against html-validate 11.21.0, and each is broader
# than what it misreads, never narrower:
#
# - ``*`` and ``?`` anywhere (``?(...)`` and ``*(...)`` extglobs included).
# - ``+(``, ``@(`` and ``!(`` extglobs. A bare parenthesis is literal, so
#   SvelteKit route groups like ``(app)/+page.svelte`` and ``page (1).html``
#   are read as written.
# - A ``[`` with any later ``]`` (a character class). A lone ``[`` or ``]``
#   is literal; ``[].html`` is also literal but is held back anyway.
# - A ``{`` with a later ``}`` and a ``,`` or ``..`` anywhere between them
#   (brace expansion). ``{a}`` and a lone brace are literal.
# - Any control character (C0, DEL or C1), outright. A POSIX file name can
#   hold one, and a newline or tab inside ``[...]`` still forms a class that
#   matches other files. Such names are pathological, so they are held back
#   wherever the character appears.
#
# Every span check runs with ``re.DOTALL``, so a delimiter pair is found
# across a newline. The backslash only matters on POSIX, where it can be part
# of a file name; on Windows it is always a separator and the conversion is
# correct.
_SELF_MATCH_BREAKER: re.Pattern[str] = re.compile(
    r"[*?]|[+@!]\(|\[.*\]|\{.*(?:,|\.\.).*\}|[\x00-\x1f\x7f-\x9f]",
    re.DOTALL,
)


# How many held-back files have their own reason spelled out.
_REASONS_SHOWN: int = 5


def _glob_reason(
    paths: list[str],
    *,
    hold_back_reason: Callable[[str], str | None],
) -> str:
    """Name the paths withheld from html-validate, and why stdin was not used.

    Args:
        paths: Relative paths html-validate's glob expansion would misread.
        hold_back_reason: The reason stdin could not validate a path.

    Returns:
        A reason naming the count, the first path, and the reason that
        applied to each of the first few paths.
    """
    shown = "; ".join(
        f"{path!r}: {hold_back_reason(path) or 'not validated'}"
        for path in paths[:_REASONS_SHOWN]
    )
    more = len(paths) - _REASONS_SHOWN
    if more > 0:
        shown = f"{shown}; and {more} more"
    return unread_paths_reason(
        tool="html-validate",
        paths=paths,
        why=(
            "html-validate expands each path as a glob, and glob, extglob or "
            "backslash characters in a name can make it skip the file or "
            "validate a different one. Such a file is validated through "
            f"stdin instead, which was not possible here ({shown})."
        ),
    )


def _reported_inspection(output: str) -> bool | None:
    """Read coverage from JSON file reports, preserving absent evidence.

    Args:
        output: JSON formatter stdout, separate from stderr diagnostics.

    Returns:
        True when a file report establishes inspection, otherwise None.
        An empty diagnostics array alone does not establish file coverage.
    """
    try:
        reports = json.loads(output)
    except ValueError:
        return None
    if isinstance(reports, list) and any(
        isinstance(report, dict)
        and isinstance(report.get("filePath"), str)
        and report["filePath"]
        and isinstance(report.get("messages"), list)
        for report in reports
    ):
        return True
    return None


def ignored_every_file(
    *,
    success: bool,
    stdout: str,
    stderr: str,
    file_args: list[str],
) -> bool:
    r"""Report whether html-validate's own config ignored every given file.

    html-validate has no option to accept an empty input. When every file
    is excluded by ``.htmlvalidateignore`` it prints no report, names the
    unmatched patterns on stderr and exits 1. lintro passes only files it
    discovered on disk, so that message means the files were ignored, not
    missing, but only when every argument matches itself: html-validate
    expands its arguments as globs, so a literal ``[slug].html`` or
    ``dir\\file.html`` does not match itself and fails with the same message
    (#3393).

    Args:
        success: Whether html-validate exited 0.
        stdout: The JSON formatter's stdout.
        stderr: html-validate's diagnostics.
        file_args: The paths passed to html-validate.

    Returns:
        True for a failed run with no report, only self-matching arguments,
        and a stderr line that starts with the no-files-matching message.
    """
    if success or stdout.strip():
        return False
    if not all(matches_itself(path) for path in file_args):
        return False
    return any(
        line.startswith(HTML_VALIDATE_NO_FILES_MATCHING) for line in stderr.splitlines()
    )


def matches_itself(argument: str, *, sep: str | None = None) -> bool:
    """Report whether html-validate's glob expansion maps a path to itself.

    Args:
        argument: A literal path passed to html-validate.
        sep: The platform path separator; ``os.sep`` when omitted. A
            backslash is unsafe only where it is not the separator.

    Returns:
        False when the path holds a glob, extglob, character-class or brace
        expansion shape, or a backslash on POSIX, that can change what it
        matches.
    """
    if _SELF_MATCH_BREAKER.search(argument):
        return False
    return (os.sep if sep is None else sep) == "\\" or "\\" not in argument


def contains_glob_syntax(argument: str) -> bool:
    """Report whether an argument would be interpreted as a glob pattern.

    Args:
        argument: Command-line argument destined for html-validate.

    Returns:
        True when the argument contains glob metacharacters.
    """
    return any(char in _GLOB_METACHARACTERS for char in argument)


def _naming(target: str, result: ToolResult) -> ToolResult:
    """Start a failed stdin call's reason with the file it was for.

    Args:
        target: The file the call validated.
        result: The call's result.

    Returns:
        The result, its output prefixed with the path when the call timed
        out or gave no verdict; a lint verdict is returned unchanged.
    """
    if not (result.execution_failed or result.timed_out):
        return result
    return replace(result, output=f"{target}: {result.output or 'no output'}")


@dataclass(frozen=True)
class _Invocation:
    """The html-validate command shared by every call of one run.

    Attributes:
        cmd: The resolved executable plus ``--formatter json``.
        used_registry_fallback: Whether resolution landed on ``bunx``/``npx``.
        fallback_command: The ``[runner, spec]`` prefix, for install guidance.
        resolved: The command exactly as resolved, as the version gate saw
            it; the stdin route checks it again from each stdin directory.
    """

    cmd: list[str]
    used_registry_fallback: bool
    fallback_command: list[str]
    resolved: list[str]


@register_tool
@dataclass
class HtmlValidatePlugin(BaseToolPlugin):
    """html-validate HTML validator plugin.

    Integrates html-validate with Lintro for checking HTML documents for
    validity, best practices, and accessibility issues. Check-only: the tool
    ships no autofixer.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="html_validate",
            description=(
                "Offline HTML validator for standards, best practices, and "
                "accessibility (WCAG) checks"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=HTML_VALIDATE_FILE_PATTERNS,
            claims=[
                Claim(
                    patterns=HTML_VALIDATE_FILE_PATTERNS,
                    capabilities={Cap.CHECK},
                ),
            ],
            reads_tree=True,
            partitionable=True,
            native_configs=list(HTML_VALIDATE_CONFIG_FILENAMES),
            version_command=["html-validate", "--version"],
            min_version=get_min_version(ToolName.HTML_VALIDATE),
            default_options={
                "timeout": HTML_VALIDATE_DEFAULT_TIMEOUT,
            },
            default_timeout=HTML_VALIDATE_DEFAULT_TIMEOUT,
        )

    def doc_url(self, code: str) -> str | None:
        """Return the html-validate documentation URL for a rule code.

        html-validate rule identifiers may be simple (``no-implicit-close``) or
        namespaced (``wcag/h37``); both map directly into the rule doc URL path.

        Args:
            code: html-validate rule identifier.

        Returns:
            URL to the rule documentation, or None when no code is available.
        """
        if code:
            return DocUrlTemplate.HTML_VALIDATE.format(code=code)
        return None

    @staticmethod
    def _append_fallback_guidance(
        *,
        output: str | None,
        used_registry_fallback: bool,
        command: list[str],
    ) -> str | None:
        """Append registry-fallback install guidance to a failure message.

        Without this the user sees html-validate's raw error with no hint that
        a project-local devDependency is the supported configuration (#1767).

        Args:
            output: Existing failure output, if any.
            used_registry_fallback: Whether the tool was invoked through
                ``bunx``/``npx`` rather than a local or PATH install.
            command: The ``[runner, spec]`` prefix of the resolved command.

        Returns:
            The output with guidance appended, unchanged when the fallback was
            not used.
        """
        if not used_registry_fallback:
            return output
        guidance = registry_fallback_guidance(command)
        return f"{output}\n\n{guidance}" if output else guidance

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with html-validate.

        Paths html-validate's glob expansion maps to themselves are passed
        as positional arguments in one call. A glob-named path is never
        passed that way: each is validated through stdin under its real
        path (#3396), and one stdin cannot handle is held back (#3393).

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use shared preparation for version check, path validation, file discovery
        ctx = self.prepare(paths, options)
        if isinstance(ctx, ToolResult):
            return ctx
        invocation = self._invocation(ctx)
        route = StdinRoute(cwd=ctx.cwd, command=invocation.resolved)
        return run_on_readable_paths(
            ctx,
            readable=lambda path: (
                matches_itself(path) or route.hold_back_reason(path) is None
            ),
            reason=lambda unread: _glob_reason(
                unread,
                hold_back_reason=route.hold_back_reason,
            ),
            run=lambda kept: self._check_kept(kept, invocation=invocation, route=route),
            unrun=lambda reason, cwd: incomplete_run_result(
                name=self.definition.name,
                reason=reason,
                cwd=cwd,
            ),
        )

    def _invocation(self, ctx: PreparedExecution) -> _Invocation:
        """Resolve the html-validate command shared by every call of a run.

        Args:
            ctx: The prepared execution context.

        Returns:
            The command with the JSON formatter, and fallback details.
        """
        # Resolve executable (local/PATH/pinned bunx). ``_get_executable_command``
        # never yields an unpinned ``@latest`` spec for any Node tool (#1811).
        #
        # Pass ctx.cwd so node_modules/.bin resolves from the directory the
        # tool actually runs in. Without it the search starts at lintro's own
        # working directory, which misses the target project's lockfile-pinned
        # html-validate and can select an unrelated install ahead of PATH,
        # making the result depend on where lintro was invoked from (#1727).
        # Precedence is target-local, then PATH, then the pinned bunx/npx spec.
        #
        # ctx.cwd is str | None; None means the process cwd, which is the
        # default resolution, so that path is unchanged.
        cmd: list[str] = self._get_executable_command(
            tool_name="html_validate",
            cwd=ctx.cwd,
        )
        # Remember whether resolution landed on the fragile registry runner so
        # a failure can be reported with install guidance instead of
        # html-validate's raw error (#1767). Checked before the command grows
        # extra arguments; only the runner and spec matter.
        return _Invocation(
            cmd=[*cmd, "--formatter", "json"],
            used_registry_fallback=is_registry_fallback_command(cmd),
            fallback_command=list(cmd[:2]),
            resolved=list(cmd),
        )

    def _check_kept(
        self,
        ctx: PreparedExecution,
        *,
        invocation: _Invocation,
        route: StdinRoute,
    ) -> ToolResult:
        """Check the files lintro can inspect, merging every call's result.

        Args:
            ctx: Prepared context holding only paths that either match
                themselves or can be validated through stdin.
            invocation: The resolved html-validate command.
            route: Decides and locates the stdin-validated files.

        Returns:
            One ToolResult over the positional call and every stdin call.
        """
        passed = ctx.rel_files or ctx.files
        plain = [index for index, path in enumerate(passed) if matches_itself(path)]
        results: list[ToolResult] = []
        if len(plain) == len(passed):
            return self._check_files(ctx, invocation=invocation)
        if plain:
            results.append(
                self._check_files(
                    replace(
                        ctx,
                        files=[ctx.files[index] for index in plain],
                        rel_files=(
                            [ctx.rel_files[index] for index in plain]
                            if ctx.rel_files
                            else []
                        ),
                    ),
                    invocation=invocation,
                ),
            )
        pending = [path for path in passed if not matches_itself(path)]
        if results and results[-1].timed_out:
            # One deadline already passed; starting the rest could take
            # the per-call timeout once per remaining file.
            results.append(self._not_started(pending, cwd=ctx.cwd))
            return merge_results(results, name=self.definition.name, cwd=ctx.cwd)
        # ``run_on_readable_paths`` judged every pending path, and with it
        # each directory's npx pin check, before this method was called; the
        # workers only open files and run html-validate (#3433).
        targets = {path: str(route.absolute(path)) for path in pending}
        # npx installs a cold package on first use, and concurrent installs
        # into one cache can leave a call with no report. Its files run one
        # at a time per pinned spec until a call parses a real report. One
        # run resolves one command, so every file shares that spec; the
        # per-directory pin check above made sure each runs it.
        npx_spec = (
            registry_fallback_spec(invocation.resolved)
            if invocation.resolved and os.path.basename(invocation.resolved[0]) == "npx"
            else None
        )
        finished, unstarted = run_stdin_calls(
            pending,
            lambda path: self._check_stdin(
                targets[path],
                invocation=invocation,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            ),
            workers=stdin_workers(),
            group=lambda _path: npx_spec,
            ready=(
                ran_html_validate if npx_spec is not None else finished_without_timeout
            ),
        )
        results.extend(finished)
        if unstarted:
            results.append(self._not_started(unstarted, cwd=ctx.cwd))
        return merge_results(results, name=self.definition.name, cwd=ctx.cwd)

    def _not_started(self, paths: list[str], *, cwd: str) -> ToolResult:
        """Name the stdin files skipped after an earlier call timed out.

        Args:
            paths: The files never started.
            cwd: The run's working directory.

        Returns:
            An incomplete run naming every one of them as not inspected.
        """
        named = ", ".join(repr(path) for path in paths)
        return incomplete_run_result(
            name=self.definition.name,
            reason=(
                f"html-validate was not run on {len(paths)} file(s) because an "
                f"earlier html-validate call timed out: {named}. Those files "
                "were not inspected."
            ),
            cwd=cwd,
        )

    def _check_stdin(
        self,
        target: str,
        *,
        invocation: _Invocation,
        timeout: float,
        cwd: str,
    ) -> ToolResult:
        """Validate one glob-named file through stdin under its real path.

        The call runs from the file's directory, so html-validate looks up
        its configuration exactly as it would for a positional path. The
        name is not passed at all: html-validate reports ``/dev/stdin``,
        and the real path is put on that report afterwards.

        Args:
            target: Absolute path of the file; findings are reported under it.
            invocation: The resolved html-validate command.
            timeout: Deadline for the call, in seconds.
            cwd: The run's working directory, recorded on the result.

        Returns:
            ToolResult for the one file. A file that can no longer be opened,
            or an html-validate that cannot be started, is an incomplete run;
            every failure reason starts with the file's path.
        """
        cmd = stdin_command(invocation.cmd)
        directory = os.path.dirname(target)
        logger.debug(
            f"[HtmlValidatePlugin] Running: {' '.join(cmd)} (cwd={directory})",
        )
        with ExitStack() as stack:
            try:
                source = stack.enter_context(open(target, "rb"))
            except OSError as error:
                return incomplete_run_result(
                    name=self.definition.name,
                    reason=(
                        f"{target}: the file could not be opened "
                        f"({error.strerror}), so it was not inspected."
                    ),
                    cwd=cwd,
                )
            try:
                result = self._run_subprocess_result(
                    cmd=cmd,
                    timeout=timeout,
                    cwd=directory,
                    stdin=source.fileno(),
                )
            except subprocess.TimeoutExpired:
                return _naming(
                    target,
                    self._timed_out(
                        cmd=cmd,
                        timeout=timeout,
                        cwd=cwd,
                        invocation=invocation,
                    ),
                )
            except OSError as error:
                reason = (
                    f"{target}: html-validate could not be started ({error}), "
                    "so the file was not inspected."
                )
                return incomplete_run_result(
                    name=self.definition.name,
                    reason=self._append_fallback_guidance(
                        output=reason,
                        used_registry_fallback=invocation.used_registry_fallback,
                        command=invocation.fallback_command,
                    )
                    or reason,
                    cwd=cwd,
                )
        return _naming(
            target,
            self._evaluate(
                rename_stdin_report(result, filename=target),
                invocation=invocation,
                cwd=cwd,
                file_args=[],
            ),
        )

    def _timed_out(
        self,
        *,
        cmd: list[str],
        timeout: float,
        cwd: str | None,
        invocation: _Invocation,
    ) -> ToolResult:
        """Build the result for a call that hit its deadline.

        Args:
            cmd: The command that timed out.
            timeout: The deadline, in seconds.
            cwd: Directory the call ran from.
            invocation: The resolved html-validate command.

        Returns:
            A timeout result, with install guidance on the registry fallback.
        """
        timed_out = batch_timeout_result(
            plugin=self,
            timeout=timeout,
            cmd=cmd,
            cwd=cwd,
        )
        return replace(
            timed_out,
            output=self._append_fallback_guidance(
                output=timed_out.output,
                used_registry_fallback=invocation.used_registry_fallback,
                command=invocation.fallback_command,
            ),
        )

    def _check_files(
        self,
        ctx: PreparedExecution,
        *,
        invocation: _Invocation,
    ) -> ToolResult:
        """Check the prepared files with html-validate in one call.

        Args:
            ctx: Prepared context holding only paths html-validate's glob
                expansion maps to themselves.
            invocation: The resolved html-validate command.

        Returns:
            ToolResult with check results.
        """
        logger.debug(
            f"[HtmlValidatePlugin] Discovered {len(ctx.files)} files matching "
            f"patterns: {self.definition.file_patterns}",
        )
        if ctx.files:
            logger.debug(
                f"[HtmlValidatePlugin] Files to check (first 10): {ctx.files[:10]}",
            )
        logger.debug(f"[HtmlValidatePlugin] Working directory: {ctx.cwd}")

        # Always pass the literal files lintro discovered, never a glob or a
        # directory: html-validate expands those through ``fs.globSync``, which
        # aborts the whole run on Bun builds that lack it (issue #1727).
        # A name starting with "-" would be read as an option; "./" keeps it
        # a path, and html-validate still reports it under its full path.
        file_args: list[str] = [
            f"./{path}" if path.startswith("-") else path
            for path in ctx.rel_files or ctx.files
        ]
        cmd = [*invocation.cmd, *file_args]

        logger.debug(
            f"[HtmlValidatePlugin] Running: {' '.join(cmd)} (cwd={ctx.cwd})",
        )

        try:
            result = self._run_subprocess_result(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
            )
        except subprocess.TimeoutExpired:
            return self._timed_out(
                cmd=cmd,
                timeout=ctx.timeout,
                cwd=ctx.cwd,
                invocation=invocation,
            )
        return self._evaluate(
            result,
            invocation=invocation,
            cwd=ctx.cwd,
            file_args=file_args,
        )

    def _evaluate(
        self,
        result: SubprocessResult,
        *,
        invocation: _Invocation,
        cwd: str | None,
        file_args: list[str],
    ) -> ToolResult:
        """Turn one html-validate call into a ToolResult.

        Args:
            result: The finished call.
            invocation: The resolved html-validate command.
            cwd: Directory the call ran from.
            file_args: Positional paths passed; empty for a stdin call.

        Returns:
            ToolResult for the call.
        """
        if file_args and ignored_every_file(
            success=result.success,
            stdout=result.stdout,
            stderr=result.stderr,
            file_args=file_args,
        ):
            return ToolResult.from_outcome(
                ToolOutcome(execution=NoFiles()),
                name=self.definition.name,
                output=HTML_VALIDATE_ALL_IGNORED_MESSAGE,
                cwd=cwd,
            )

        # JSON is written to stdout; stderr carries diagnostics only.
        issues, parse_failures = parse_html_validate_output_with_stats(
            output=result.stdout,
        )
        issues_count: int = len(issues)

        # html-validate prints a JSON array on every completed run (``[]``
        # when clean) and exits non-zero whenever it reports issues. No report,
        # a malformed one, or a failure with nothing parsed is no verdict and
        # proves no coverage (#3352). A failure with nothing parsed is the case
        # worth explaining: the registry fallback could not run.
        # A complete report naming a file proves coverage even when that
        # file has no messages, whatever the exit status.
        gap = report_gap(
            tool="html-validate",
            output=result.stdout,
            run_success=result.success,
            found_issues=bool(issues)
            or (not parse_failures and bool(_reported_inspection(result.stdout))),
            parse_failures=parse_failures,
        )
        if gap is not None:
            reason, failures = gap
            if not result.success and not issues:
                if result.stderr.strip():
                    reason = f"{reason}\n{tool_message_excerpt(result.stderr)}"
                reason = (
                    self._append_fallback_guidance(
                        output=reason,
                        used_registry_fallback=invocation.used_registry_fallback,
                        command=invocation.fallback_command,
                    )
                    or reason
                )
            return incomplete_run_result(
                name=self.definition.name,
                reason=reason,
                issues=issues,
                parse_failures=failures,
                cwd=cwd,
            )

        success_flag: bool = result.success and issues_count == 0
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(
                    success=success_flag,
                    inspected_files=(
                        True if result.success else _reported_inspection(result.stdout)
                    ),
                ),
                issues_count=issues_count,
            ),
            name=self.definition.name,
            output=None if success_flag else result.output,
            issues=issues,
            cwd=cwd,
            parse_failures_count=parse_failures,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """html-validate cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: html-validate is a validator only and cannot
                fix issues.
        """
        raise NotImplementedError(
            "html-validate cannot fix issues; it only validates HTML.",
        )
