"""Per-review comment body for GitHub AI reviews (epic #1905, issue #1910).

The review body is the surface posted *with each round*: it describes this
round only. The sticky comment owns cumulative status and history; the inline
comments own per-finding detail. So this body carries exactly four things —
what this round found (header + resolved delta), what to do about it (the
scoped fix prompt), what the run cost (visible stats plus the config that
produced them), and what it looked at (commits and files, with a reason for
every file it did not look at).

The fix panel is unconditional. Even when this round's findings are *exactly*
the PR's still-open findings — round 1, or a round where nothing was carried
over — the panel renders in full rather than pointing at the sticky comment's
fix-all: a reader on the review must be able to copy the prompt where the
findings are announced, without a navigation hop (#1956). The panel's footer
still names the sticky's fix-all for everything open across all rounds.
"""

from __future__ import annotations

from lintro import __version__ as lintro_version
from lintro.ai.resolved_ai_config import format_sourced_value
from lintro.ai.review.agent_prompts import (
    prompt_findings,
    render_agent_prompt_panel,
)
from lintro.ai.review.enums.agent_prompt_scope_kind import AgentPromptScopeKind
from lintro.ai.review.github_constants import MAX_COMMENT_CHARS
from lintro.ai.review.github_render import (
    format_badge_tables,
    run_stats_primary_cells,
    sanitize_comment_text,
)
from lintro.ai.review.models.agent_prompt_scope import AgentPromptScope
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.skipped_file import SkippedFile

__all__ = ["REVIEW_BODY_FOOTER", "build_review_body"]

#: Footer cross-linking the two surfaces this body deliberately does not own.
REVIEW_BODY_FOOTER = (
    "<sub>🤖 lintro · cumulative status &amp; history → sticky comment · "
    "finding details → inline comments below</sub>"
)

#: Characters of a commit sha rendered in prose.
_SHORT_SHA = 7

#: Maximum file entries listed before the files collapsible summarizes the rest.
_MAX_LISTED_FILES = 60

_TRUNCATION_NOTICE = "\n\n> ✂️ Comment truncated to fit GitHub's size limit."


def build_review_body(
    *,
    result: ReviewResult,
    prior_state: ReviewState,
    match: FindingMatchResult,
    head_sha: str = "",
    transport: str = "",
    auth_mode: str = "",
    config_source: str = "",
    new_commits: int | None = None,
) -> str:
    """Compose the body posted alongside this round's inline comments.

    Args:
        result: This round's review result.
        prior_state: State decoded from the sticky comment before this round.
        match: Cross-round matching outcome for this round's findings.
        head_sha: Head commit sha reviewed in this round. Falls back to the
            metadata's head ref.
        transport: Provider transport used for this round (e.g. ``cli``).
        auth_mode: Authentication mode used by the transport.
        config_source: Human-readable description of where this run's settings
            came from. Omitted from the body when empty.
        new_commits: Number of commits pushed since the previous round. ``None``
            when it could not be determined; the provenance sentence then omits
            the count rather than guessing one.

    Returns:
        Markdown body for the review, capped to GitHub's comment size limit.
    """
    round_number = prior_state.next_round
    sections = [
        _header(
            result=result,
            match=match,
            prior_state=prior_state,
            round_number=round_number,
            head_sha=head_sha,
        ),
        _prompt_section(
            result=result,
            round_number=round_number,
        ),
        _run_stats_section(
            result=result,
            transport=transport,
            auth_mode=auth_mode,
            config_source=config_source,
        ),
        _commits_section(
            result=result,
            prior_state=prior_state,
            round_number=round_number,
            head_sha=head_sha,
            new_commits=new_commits,
        ),
        _files_section(result=result),
        REVIEW_BODY_FOOTER,
    ]
    body = "\n\n".join(section for section in sections if section)
    return _cap(body=body)


def _short(sha: str) -> str:
    """Return a display-length commit sha.

    Args:
        sha: Full or already-short commit sha.

    Returns:
        The sha truncated to :data:`_SHORT_SHA` characters, sanitized.
    """
    return sanitize_comment_text(sha, limit=64)[:_SHORT_SHA]


def _plural(*, count: int, noun: str) -> str:
    """Format a count with a naively pluralized noun.

    Args:
        count: Number of items.
        noun: Singular noun to pluralize with a trailing ``s``.

    Returns:
        The count and noun, e.g. ``"1 finding"``.
    """
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _prior_sha(*, prior_state: ReviewState) -> str:
    """Return the head sha of the most recent prior round, if any."""
    return prior_state.runs[-1].sha if prior_state.runs else ""


def _header(
    *,
    result: ReviewResult,
    match: FindingMatchResult,
    prior_state: ReviewState,
    round_number: int,
    head_sha: str,
) -> str:
    """Render the one-line findings header with the resolved delta.

    The resolved segment is rendered on every round after the first, even at
    zero: omitting it would make "nothing was fixed since last round" and
    "there was no last round" look identical.

    Args:
        result: This round's review result.
        match: Cross-round matching outcome for this round.
        prior_state: State before this round.
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round.

    Returns:
        Markdown header line.
    """
    head = _short(head_sha or result.metadata.head_ref)
    base = _short(_prior_sha(prior_state=prior_state) or result.metadata.base_ref)
    parts = [
        f"🔎 **Lintro review — {_plural(count=len(result.findings), noun='finding')} "
        "posted**",
    ]
    if round_number > 1:
        parts.append(
            f"✔ {len(match.resolved)} resolved since round {round_number - 1}",
        )
    parts.append(f"round {round_number}")
    if base and head:
        parts.append(f"commits `{base}..{head}`")
    return " · ".join(parts)


def _prompt_section(
    *,
    result: ReviewResult,
    round_number: int,
) -> str:
    """Render this round's scoped fix prompt panel.

    The panel is rendered on every round, including one whose findings are
    exactly the PR's still-open set: the review comment must be self-contained
    (#1956). The panel's footer still sends readers to the sticky comment's
    fix-all for everything open across all rounds.

    Args:
        result: This round's review result.
        round_number: 1-based round number for this run.

    Returns:
        Markdown for the prompt panel; empty when the round produced nothing
        actionable to fix.
    """
    if not prompt_findings(findings=result.findings):
        return ""
    return render_agent_prompt_panel(
        findings=result.findings,
        scope=AgentPromptScope(
            kind=AgentPromptScopeKind.THIS_REVIEW,
            round_number=round_number,
        ),
    )


def _run_stats_section(
    *,
    result: ReviewResult,
    transport: str,
    auth_mode: str,
    config_source: str,
) -> str:
    """Render the visible run-stats block and the config-source note.

    Stats follow the epic's shared ordering rule: model, est. cost, tokens in,
    tokens out on the first line; mechanics on the second. ``~`` marks values
    estimated locally, so a subscription run never presents an estimate as a
    billed figure. Both rows are drawn by the shared badge-table renderer, and
    the primary row's cells come from the shared ``run_stats_primary_cells``,
    so the model, cost, and token figures cannot drift from the sticky's
    ``This run`` section (#1955). The secondary rows differ by design: this
    surface carries ``strictness`` and the ``lintro`` version, which the
    sticky's leaner status board omits.

    Args:
        result: This round's review result.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        config_source: Human-readable description of the settings' origin.

    Returns:
        Markdown for the run-stats section.
    """
    metadata = result.metadata
    primary = run_stats_primary_cells(metadata=metadata)

    transport_label = format_sourced_value(
        sanitize_comment_text(transport, limit=40),
        metadata.transport_source or None,
    )
    if transport_label and auth_mode:
        transport_label = (
            f"{transport_label} · {sanitize_comment_text(auth_mode, limit=40)}"
        )
    secondary: list[tuple[str, str]] = []
    if transport_label:
        secondary.append(("transport", transport_label))
    secondary.extend(
        [
            ("depth", str(metadata.depth)),
            ("strictness", sanitize_comment_text(metadata.strictness, limit=40)),
            ("files", str(metadata.files_reviewed)),
            ("checks", str(metadata.checklist_items)),
            ("duration", f"{metadata.duration_seconds:.0f}s"),
            ("lintro", lintro_version),
        ],
    )

    lines = ["**📊 Run stats**", ""]
    lines.extend(format_badge_tables(rows=[primary, secondary]))
    if config_source:
        source = sanitize_comment_text(config_source, limit=300)
        lines.extend(["", f"<sub>Config source: {source}</sub>"])
    return "\n".join(lines)


def _commits_section(
    *,
    result: ReviewResult,
    prior_state: ReviewState,
    round_number: int,
    head_sha: str,
    new_commits: int | None = None,
) -> str:
    """Render the commits collapsible describing this round's provenance.

    Args:
        result: This round's review result.
        prior_state: State before this round.
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round.
        new_commits: Number of commits pushed since the previous round, or
            ``None`` when unknown.

    Returns:
        Markdown for the ``📥 Commits`` collapsible.
    """
    metadata = result.metadata
    head = _short(head_sha or metadata.head_ref)
    base = _short(metadata.base_ref)
    prior = _short(_prior_sha(prior_state=prior_state))

    if prior and prior != head:
        commits = (
            _plural(count=new_commits, noun="new commit")
            if new_commits is not None
            else "new commits"
        )
        sentence = (
            f"This round reviewed the {commits} since round {round_number - 1}, "
            f"`{prior}` → `{head}`, in the context of the PR's full diff "
            f"against `{base}`."
        )
    else:
        sentence = (
            f"This round reviewed the PR's full diff against `{base}` " f"at `{head}`."
        )
    return "\n".join(
        [
            "<details><summary>📥 Commits</summary>",
            "",
            sentence,
            "",
            "</details>",
        ],
    )


def _skipped_line(*, entry: SkippedFile) -> str:
    """Render one skipped file with the reason it was skipped."""
    path = sanitize_comment_text(entry.path, limit=300)
    # The reason carries a file path in its detail, so it is sanitized on the
    # same terms as the path itself rather than trusted because it is
    # partly-canned text.
    label = sanitize_comment_text(entry.label, limit=300)
    return f"- `{path}` — skipped ({label})"


def _partial_run_warning(*, result: ReviewResult) -> str:
    """Warn that a stopped-early run may not have covered the listed files.

    A partial run's chunks are reviewed concurrently, so which files the
    completed chunks covered is not recoverable per-file. Naming the
    uncertainty is honest; listing a precise "reviewed" set that may be wrong
    is not.

    Args:
        result: This round's review result.

    Returns:
        A warning line, or an empty string when the run completed.
    """
    metadata = result.metadata
    if not metadata.partial:
        return ""
    reason = sanitize_comment_text(metadata.stopped_reason or "incomplete", limit=60)
    return (
        f"> ⚠️ Review stopped early ({reason}) after "
        f"{metadata.chunks_reviewed} of {metadata.chunks_total} chunks — some "
        "files listed below may not have been reviewed in full."
    )


def _files_section(*, result: ReviewResult) -> str:
    """Render the files-reviewed collapsible, with per-file skip reasons.

    Args:
        result: This round's review result.

    Returns:
        Markdown for the ``📁 Files reviewed`` collapsible; empty when the run
        recorded neither reviewed nor skipped paths.
    """
    metadata = result.metadata
    reviewed = list(metadata.reviewed_paths)
    skipped = list(metadata.skipped_files)
    if not reviewed and not skipped:
        return ""

    summary = f"📁 Files reviewed ({len(reviewed)})"
    if skipped:
        summary += f" · {len(skipped)} skipped"

    # Reviewed and skipped files get independent budgets. Sharing one would let
    # a wide PR's reviewed list crowd out the skip lines, and the skips are the
    # half a reader cannot reconstruct from the diff.
    warning = _partial_run_warning(result=result)
    entries = [*([warning, ""] if warning else [])]
    entries.extend(
        f"- `{sanitize_comment_text(path, limit=300)}`"
        for path in reviewed[:_MAX_LISTED_FILES]
    )
    hidden_reviewed = max(len(reviewed) - _MAX_LISTED_FILES, 0)
    if hidden_reviewed:
        entries.append(f"- …and {hidden_reviewed} more reviewed")
    entries.extend(_skipped_line(entry=entry) for entry in skipped[:_MAX_LISTED_FILES])
    hidden_skipped = max(len(skipped) - _MAX_LISTED_FILES, 0)
    if hidden_skipped:
        entries.append(f"- …and {hidden_skipped} more skipped")

    return "\n".join(
        [f"<details><summary>{summary}</summary>", "", *entries, "", "</details>"],
    )


def _cap(*, body: str) -> str:
    """Trim an over-long body, leaving an explicit truncation marker.

    Args:
        body: Assembled review body.

    Returns:
        The body, truncated with a visible notice when over the size cap.
    """
    if len(body) <= MAX_COMMENT_CHARS:
        return body
    keep = MAX_COMMENT_CHARS - len(_TRUNCATION_NOTICE)
    return body[:keep].rstrip() + _TRUNCATION_NOTICE
