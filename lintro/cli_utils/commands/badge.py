"""``lintro badge`` command for shields.io health-score badges."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import click

from lintro.api import core as api
from lintro.models.core.run_artifact import RunArtifact
from lintro.utils.health_score import (
    MAX_SCORE,
    MIN_SCORE,
    build_shields_badge_markdown,
    build_shields_badge_url,
    shields_color_for_tier,
    tier_for_score,
)

_SHIELDS_STYLES: tuple[str, ...] = (
    "flat",
    "flat-square",
    "plastic",
    "for-the-badge",
    "social",
)


def _live_score_is_usable(artifact: RunArtifact) -> bool:
    """Return whether a live check produced a badge-worthy score.

    Args:
        artifact: Completed check run.

    Returns:
        bool: ``True`` when at least one tool executed and a health score
        was computed. Empty, all-skipped, filtered, and early-exit runs are
        not usable public quality signals.
    """
    if artifact.early_exit or artifact.health is None:
        return False
    if artifact.main_phase_empty_due_to_filter:
        return False
    return any(not result.skipped for result in artifact.tool_results)


def resolve_health_score(
    *,
    score_override: int | None,
    paths: tuple[str, ...],
) -> int:
    """Resolve the project health score for badge generation.

    When ``score_override`` is set, that value is returned directly (useful for
    tests and CI snippets). Otherwise a check is run via :func:`api.check_run`
    and the score is read from the :class:`~lintro.models.core.run_artifact.RunArtifact`
    (issue #1823) rather than re-parsing stdout.

    Args:
        score_override: Explicit score to use instead of running tools.
        paths: Paths to check when computing a live score.

    Returns:
        int: Health score in ``[0, 100]``.

    Raises:
        click.ClickException: If the live check exits before a score is
            produced, or no tool actually executed (empty / all-skipped).
    """
    if score_override is not None:
        return score_override

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        artifact = api.check_run(
            paths=list(paths) if paths else None,
            no_log=True,
            score=True,
        )
    if not _live_score_is_usable(artifact):
        raise click.ClickException(
            "Could not determine a usable health score because the check "
            "exited early, was empty, or all tools were skipped.",
        )
    return artifact.health_score


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
    "--score",
    "score_override",
    type=click.IntRange(MIN_SCORE, MAX_SCORE),
    default=None,
    help="Use this score instead of running a check (0-100).",
)
def badge_command(
    paths: tuple[str, ...],
    style: str | None,
    url_only: bool,
    json_output: bool,
    score_override: int | None,
) -> None:
    """Generate a shields.io markdown badge for the project health score.

    Runs a score-only check on the given paths (default ``.``) unless
    ``--score`` supplies an override. Prints a markdown image by default;
    use ``--url`` for the bare URL or ``--json`` for structured output.
    \u000c

    Args:
        paths: File/directory paths to score; empty means the current directory.
        style: Optional shields.io style query parameter.
        url_only: Emit only the badge URL.
        json_output: Emit JSON with score, tier, color, url, and markdown.
        score_override: Explicit score that skips running tools.

    Raises:
        click.UsageError: If ``--json`` and ``--url`` are passed together.
    """
    if json_output and url_only:
        raise click.UsageError("Use --json or --url, not both.")

    score = resolve_health_score(score_override=score_override, paths=paths)
    tier = tier_for_score(score)
    color = shields_color_for_tier(tier)
    url = build_shields_badge_url(score, style=style)
    markdown = build_shields_badge_markdown(score, style=style)

    if json_output:
        payload = {
            "score": score,
            "tier": tier.label,
            "color": color,
            "url": url,
            "markdown": markdown,
        }
        click.echo(json.dumps(payload, indent=2))
        return

    if url_only:
        click.echo(url)
        return

    click.echo(markdown)
