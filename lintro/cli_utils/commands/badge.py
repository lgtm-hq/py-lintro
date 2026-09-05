"""``lintro badge`` command for shields.io issue-count badges.

The badge used to publish the 0-100 health score. Issue #1739 deleted that
score because it had no size normalization, so the command was retargeted
rather than removed: it now publishes the run's severity counts, which mean
the same thing in every repository.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stdout
from urllib.parse import quote

import click

from lintro.api import core as api
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.severity_counts import SeverityCounts
from lintro.models.core.tool_result import ToolResult

_SHIELDS_STYLES: tuple[str, ...] = (
    "flat",
    "flat-square",
    "plastic",
    "for-the-badge",
    "social",
)

# shields.io colour tokens. A clean run is bright green, warnings and info
# findings are yellow, and any error is red — the same three-way split the old
# score tiers used, now driven by what was actually found.
COLOR_CLEAN: str = "brightgreen"
COLOR_WARNINGS: str = "yellow"
COLOR_ERRORS: str = "red"

# Real wrapper messages vary: "No files found to check.", "No Astro files to
# check.", "No .py/.pyi files found to check.".
_NO_FILES_CHECKED_RE = re.compile(r"(?i)\bno\b.*\bfiles?\b.*\bto check\b")


def _result_checked_any_files(result: ToolResult) -> bool:
    """Return whether a tool result looks like it actually inspected files.

    Tools that run over an empty path still return ``skipped=False`` and
    ``success=True`` with a ``"No … files found to check"`` message. That is
    not a public quality signal — it is the same as never running.

    Args:
        result: One tool's completed result.

    Returns:
        bool: ``True`` when the tool was not skipped and did not report an
        empty file set.
    """
    if result.skipped or result.timed_out:
        return False
    text = f"{result.output or ''}\n{result.formatted_output or ''}"
    return _NO_FILES_CHECKED_RE.search(text) is None


def _live_counts_are_usable(artifact: RunArtifact) -> bool:
    """Return whether a live check produced badge-worthy counts.

    Args:
        artifact: Completed check run.

    Returns:
        bool: ``True`` when at least one tool inspected files. Empty,
        all-skipped, timed-out, and early-exit runs are not usable public
        quality signals. A filter-empty main phase is still usable when
        post-checks produced a real result.
    """
    if artifact.early_exit:
        return False
    return any(_result_checked_any_files(result) for result in artifact.tool_results)


def badge_color(counts: SeverityCounts) -> str:
    """Return the shields.io colour token for a set of severity counts.

    Args:
        counts: Severity tallies the badge reports.

    Returns:
        str: ``brightgreen`` for a clean run, ``red`` when any error was
        found, and ``yellow`` when only warnings or info issues remain.
    """
    if counts.errors:
        return COLOR_ERRORS
    if counts.total:
        return COLOR_WARNINGS
    return COLOR_CLEAN


def badge_message(counts: SeverityCounts) -> str:
    """Return the human-readable badge message for a set of severity counts.

    Args:
        counts: Severity tallies the badge reports.

    Returns:
        str: ``"0 issues"`` for a clean run, otherwise a per-severity summary
        listing only the severities that were found, such as
        ``"3 errors, 1 warning"``.
    """
    if not counts.total:
        return "0 issues"
    parts = [
        f"{value} {noun if value == 1 else noun + 's'}"
        for value, noun in (
            (counts.errors, "error"),
            (counts.warnings, "warning"),
        )
        if value
    ]
    if counts.info:
        parts.append(f"{counts.info} info")
    return ", ".join(parts)


def build_shields_badge_url(
    counts: SeverityCounts,
    *,
    style: str | None = None,
) -> str:
    """Build a shields.io static badge URL for a run's severity counts.

    Args:
        counts: Severity tallies the badge reports.
        style: Optional shields.io style (e.g. ``flat``); omitted when
            ``None``.

    Returns:
        str: Absolute shields.io badge URL.
    """
    message = quote(badge_message(counts), safe="")
    url = f"https://img.shields.io/badge/lintro-{message}-{badge_color(counts)}"
    if style:
        url = f"{url}?style={quote(style, safe='-')}"
    return url


def build_shields_badge_markdown(
    counts: SeverityCounts,
    *,
    style: str | None = None,
    alt_text: str = "Lintro Issues",
) -> str:
    """Build a markdown image snippet for a severity-count shields.io badge.

    Args:
        counts: Severity tallies the badge reports.
        style: Optional shields.io style forwarded to the URL builder.
        alt_text: Alt text for the markdown image.

    Returns:
        str: Markdown such as
        ``![Lintro Issues](https://img.shields.io/badge/lintro-0%20issues-brightgreen)``.
    """
    return f"![{alt_text}]({build_shields_badge_url(counts, style=style)})"


def resolve_severity_counts(
    *,
    override: SeverityCounts | None,
    paths: tuple[str, ...],
) -> SeverityCounts:
    """Resolve the severity counts the badge reports.

    When ``override`` is set, those counts are returned directly (useful for
    tests and CI snippets). Otherwise a check is run via :func:`api.check_run`
    and the counts are read from the
    :class:`~lintro.models.core.run_artifact.RunArtifact` (issue #1823) rather
    than re-parsing stdout.

    Args:
        override: Explicit counts to use instead of running tools.
        paths: Paths to check when counting live.

    Returns:
        SeverityCounts: Counts for the badge.

    Raises:
        click.ClickException: If the live check exits before producing
            counts, or no tool actually executed (empty / all-skipped).
    """
    if override is not None:
        return override

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        artifact = api.check_run(
            paths=list(paths) if paths else None,
            no_log=True,
            ai_enabled=False,
        )
    if not _live_counts_are_usable(artifact):
        raise click.ClickException(
            "Could not determine usable issue counts because the check "
            "exited early, was empty, or all tools were skipped.",
        )
    return artifact.severity_counts


def _counts_override(
    *,
    errors: int | None,
    warnings: int | None,
    info: int | None,
) -> SeverityCounts | None:
    """Build the explicit counts requested on the command line, if any.

    Args:
        errors: Value of ``--errors``, or ``None`` when not passed.
        warnings: Value of ``--warnings``, or ``None`` when not passed.
        info: Value of ``--info``, or ``None`` when not passed.

    Returns:
        SeverityCounts | None: The override when at least one option was
        given (unset severities count as zero), otherwise ``None``.
    """
    if errors is None and warnings is None and info is None:
        return None
    return SeverityCounts(
        errors=errors or 0,
        warnings=warnings or 0,
        info=info or 0,
    )


@click.command("badge")
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--style",
    type=click.Choice(_SHIELDS_STYLES, case_sensitive=False),
    default=None,
    help="shields.io badge style (e.g. flat).",
)
@click.option(
    "--url",
    "url_only",
    is_flag=True,
    help="Print only the shields.io badge URL.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Print badge metadata as JSON.",
)
@click.option(
    "--errors",
    type=click.IntRange(min=0),
    default=None,
    help="Use this error count instead of running a check.",
)
@click.option(
    "--warnings",
    type=click.IntRange(min=0),
    default=None,
    help="Use this warning count instead of running a check.",
)
@click.option(
    "--info",
    type=click.IntRange(min=0),
    default=None,
    help="Use this info count instead of running a check.",
)
def badge_command(
    paths: tuple[str, ...],
    style: str | None,
    url_only: bool,
    json_output: bool,
    errors: int | None,
    warnings: int | None,
    info: int | None,
) -> None:
    """Generate a shields.io markdown badge for the project's issue counts.

    Runs a check on the given paths (default ``.``) unless an explicit count
    option supplies the numbers. Prints a markdown image by default; use
    ``--url`` for the bare URL or ``--json`` for structured output.
    \u000c

    Args:
        paths: File/directory paths to check; empty means the current
            directory.
        style: Optional shields.io style query parameter.
        url_only: Emit only the badge URL.
        json_output: Emit JSON with the counts, colour, url, and markdown.
        errors: Explicit ERROR count that skips running tools.
        warnings: Explicit WARNING count that skips running tools.
        info: Explicit INFO count that skips running tools.

    Raises:
        click.UsageError: If ``--json`` and ``--url`` are passed together.
    """
    if json_output and url_only:
        raise click.UsageError("Use --json or --url, not both.")

    counts = resolve_severity_counts(
        override=_counts_override(errors=errors, warnings=warnings, info=info),
        paths=paths,
    )
    url = build_shields_badge_url(counts, style=style)
    markdown = build_shields_badge_markdown(counts, style=style)

    if json_output:
        payload = {
            "counts": counts.to_dict(),
            "message": badge_message(counts),
            "color": badge_color(counts),
            "url": url,
            "markdown": markdown,
        }
        click.echo(json.dumps(payload, indent=2))
        return

    if url_only:
        click.echo(url)
        return

    click.echo(markdown)
