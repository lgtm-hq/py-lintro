"""html-validate tool definition.

html-validate is an offline HTML validator that checks HTML documents against
best practices, standards, and accessibility (WCAG) rules. It is a Node.js tool
invoked via bun/bunx, mirroring lintro's other Node tools (markdownlint,
prettier).

Executable resolution prefers a consumer-local ``node_modules/.bin`` install,
then a binary on PATH, and only then a ``bunx``/``npx`` invocation pinned to the
version in ``package.json``; ``@latest`` is never resolved at runtime. Files are
always passed as literal paths so html-validate's glob expansion never runs.

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
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass, replace

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
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
)
from lintro.tools.core.readable_paths import (
    run_on_readable_paths,
    unread_paths_reason,
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

# Characters that can stop a literal path from matching itself in
# html-validate's ``fs.globSync`` expansion. Node builds its minimatch matcher
# with ``windowsPathsNoEscape: true`` (a backslash becomes a path separator),
# extglobs enabled (``+(...)``, ``@(...)`` and the like need parentheses),
# brace expansion and character classes, but with ``nonegate`` and
# ``nocomment`` (``!`` and ``#`` stay literal). Such a path can be dropped or
# matched to a different file, so it is never passed to html-validate (#3393).
# The backslash only matters on POSIX, where it can be part of a file name; on
# Windows it is always a separator and the conversion is correct.
_SELF_MATCH_BREAKERS: frozenset[str] = _GLOB_METACHARACTERS | frozenset("()")


def _glob_reason(paths: list[str]) -> str:
    """Name the paths withheld from html-validate because of glob syntax.

    Args:
        paths: Relative paths html-validate's glob expansion would misread.

    Returns:
        A one-line reason.
    """
    return unread_paths_reason(
        tool="html-validate",
        paths=paths,
        why=(
            "html-validate expands each path as a glob, and glob, extglob or "
            "backslash characters in a name can make it skip the file or "
            "validate a different one."
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
        False when the path holds a glob, extglob or brace character, or a
        backslash on POSIX, that can change what it matches.
    """
    if any(char in _SELF_MATCH_BREAKERS for char in argument):
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
        return run_on_readable_paths(
            ctx,
            readable=matches_itself,
            reason=_glob_reason,
            run=self._check_files,
            unrun=lambda reason, cwd: incomplete_run_result(
                name=self.definition.name,
                reason=reason,
                cwd=cwd,
            ),
        )

    def _check_files(self, ctx: PreparedExecution) -> ToolResult:
        """Check the prepared files with html-validate.

        Args:
            ctx: Prepared context holding only paths html-validate's glob
                expansion maps to themselves.

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

        # Build command: resolve executable (local/PATH/pinned bunx) + JSON
        # formatter. ``_get_executable_command`` never yields an unpinned
        # ``@latest`` spec for any Node tool (see NodeJSBuilder, #1811).
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
        used_registry_fallback: bool = is_registry_fallback_command(cmd)
        fallback_command: list[str] = list(cmd[:2])
        cmd.extend(["--formatter", "json"])

        # Always pass the literal files lintro discovered, never a glob or a
        # directory: html-validate expands those through ``fs.globSync``, which
        # aborts the whole run on Bun builds that lack it (issue #1727).
        file_args: list[str] = ctx.rel_files or ctx.files
        globbed = [path for path in file_args if contains_glob_syntax(path)]
        if globbed:
            logger.warning(
                f"[HtmlValidatePlugin] {len(globbed)} path(s) contain glob "
                f"metacharacters and may be re-expanded by html-validate: "
                f"{globbed[:5]}",
            )
        cmd.extend(file_args)

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
            timed_out = batch_timeout_result(
                plugin=self,
                timeout=ctx.timeout,
                cmd=cmd,
                cwd=ctx.cwd,
            )
            return replace(
                timed_out,
                output=self._append_fallback_guidance(
                    output=timed_out.output,
                    used_registry_fallback=used_registry_fallback,
                    command=fallback_command,
                ),
            )

        if ignored_every_file(
            success=result.success,
            stdout=result.stdout,
            stderr=result.stderr,
            file_args=file_args,
        ):
            return ToolResult.from_outcome(
                ToolOutcome(execution=NoFiles()),
                name=self.definition.name,
                output=HTML_VALIDATE_ALL_IGNORED_MESSAGE,
                cwd=ctx.cwd,
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
                        used_registry_fallback=used_registry_fallback,
                        command=fallback_command,
                    )
                    or reason
                )
            return incomplete_run_result(
                name=self.definition.name,
                reason=reason,
                issues=issues,
                parse_failures=failures,
                cwd=ctx.cwd,
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
            cwd=ctx.cwd,
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
