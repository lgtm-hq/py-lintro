"""The banners a settled finding's inline thread is stamped with (#1912).

An inline finding comment is posted once and then *edited* for the rest of the
pull request's life, so the thread carries its own outcome instead of leaving
a reader to diff two rounds of comments by eye. Three stamps exist, all
written into a single replaceable block at the end of the comment body:

* ``✔ Addressed in <sha> · round N`` — every occurrence stopped reproducing.
  The comment's agent-prompt panel is retitled ``(historical)`` so nobody
  pastes a prompt for a fix that already landed, and the thread is resolved
  when ``review.auto_resolve`` allows it.
* ``✔ 14/20 addressed in <sha> · round N`` — partial progress on a collapsed
  pattern (#1925). The finding is still open, so the thread is **never**
  resolved and the prompt panel stays live.
* ``↩ Regressed in <sha> · round N`` — the finding came back. The old thread
  is stamped and *stays resolved*; the finding is re-raised on a fresh thread
  that carries the provenance back to here (mock-4 section 3, state D).

Every edit is idempotent: the block is rewritten wholesale, so re-running a
round adds no second banner. Deciding *which* thread gets which stamp is
:mod:`lintro.ai.review.lifecycle.threads`; this module only renders.
"""

from __future__ import annotations

import re

from lintro.ai.review.enums.lifecycle_stage import LifecycleStage
from lintro.ai.review.github_constants import SHORT_SHA_LENGTH
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = [
    "HISTORICAL_SUFFIX",
    "LIFECYCLE_CLOSE",
    "LIFECYCLE_OPEN",
    "apply_lifecycle_block",
    "regression_provenance",
    "render_lifecycle_block",
]

#: Delimiters of the replaceable lifecycle block at the end of a comment body.
LIFECYCLE_OPEN = "<!-- lintro-lifecycle -->"
LIFECYCLE_CLOSE = "<!-- /lintro-lifecycle -->"

#: Suffix appended to a prompt panel's title once its finding is settled.
HISTORICAL_SUFFIX = " (historical)"

_LIFECYCLE_BLOCK_RE = re.compile(
    re.escape(LIFECYCLE_OPEN) + r".*?" + re.escape(LIFECYCLE_CLOSE),
    re.DOTALL,
)
_PANEL_TITLE_RE = re.compile(r"^> ⚡ \*\*(?P<title>.+?)\*\*\s*$", re.MULTILINE)


def _short_sha(*, sha: str) -> str:
    """Return the display-length prefix of a commit sha, or an empty string."""
    return sanitize_comment_text(sha, limit=64).strip()[:SHORT_SHA_LENGTH]


def _sha_label(*, sha: str) -> str:
    """Render a commit sha for a banner, degrading when it is unknown."""
    short = _short_sha(sha=sha)
    return f"`{short}`" if short else "this round's commit"


def render_lifecycle_block(
    *,
    record: FindingRecord,
    stage: LifecycleStage,
    head_sha: str,
    round_number: int,
    new_thread_url: str = "",
) -> str:
    """Render the lifecycle block stamped onto a finding's inline comment.

    Args:
        record: Finding record the comment belongs to.
        stage: What this round verified about the finding.
        head_sha: Head commit sha reviewed in this round.
        round_number: 1-based round number for this run.
        new_thread_url: URL of the fresh thread carrying a regression, when it
            could be resolved.

    Returns:
        The delimited block, ready to be appended to (or swapped into) a
        comment body.
    """
    lines = _banner_lines(
        record=record,
        stage=stage,
        head_sha=head_sha,
        round_number=round_number,
        new_thread_url=new_thread_url,
    )
    return "\n".join([LIFECYCLE_OPEN, *lines, LIFECYCLE_CLOSE])


def _banner_lines(
    *,
    record: FindingRecord,
    stage: LifecycleStage,
    head_sha: str,
    round_number: int,
    new_thread_url: str,
) -> list[str]:
    """Render the banner blockquote lines for one lifecycle stage.

    Args:
        record: Finding record the comment belongs to.
        stage: What this round verified about the finding.
        head_sha: Head commit sha reviewed in this round.
        round_number: 1-based round number for this run.
        new_thread_url: URL of the fresh thread carrying a regression.

    Returns:
        One or two blockquote lines.
    """
    where = _sha_label(sha=head_sha)
    if stage is LifecycleStage.ADDRESSED:
        return [f"> ✔ **Addressed in {where} · round {round_number}**"]
    if stage is LifecycleStage.PARTIAL:
        return [
            f"> ✔ **{record.occurrences_addressed}/{record.occurrence_total} "
            f"addressed in {where} · round {round_number}** — the pattern is "
            "still open; this thread stays open until every occurrence is gone.",
        ]

    lines: list[str] = []
    fixed_in = _short_sha(sha=record.resolved_sha)
    if fixed_in:
        lines.append(
            f"> ✔ **Addressed in `{fixed_in}` · round {record.resolved_round}**",
        )
    # Without a url there may be no new thread at all: a finding that no longer
    # anchors to a line in the diff is re-raised on the sticky comment instead,
    # so pointing at an inline comment that does not exist would be a lie.
    pointer = (
        f"see the [new thread]({new_thread_url})"
        if new_thread_url
        else "the finding is open again — see the sticky comment's open findings"
    )
    lines.append(
        f"> ↩ **Regressed in {where} · round {round_number}** — {pointer}. "
        "This thread stays resolved.",
    )
    return lines


def regression_provenance(*, record: FindingRecord, thread_url: str = "") -> str:
    """Render the provenance note carried by a regression's fresh comment.

    Args:
        record: The regressed finding record, still carrying the round it was
            first raised in and the round that had fixed it.
        thread_url: URL of the original thread, when its comment id is known.

    Returns:
        A one-line blockquote naming the finding's history, so a reader of the
        new thread is not told a fixed finding is simply "new".
    """
    fixed_in = _short_sha(sha=record.resolved_sha)
    fixed = f"fixed round {record.resolved_round}" if record.resolved_round else "fixed"
    if fixed_in:
        fixed += f" (`{fixed_in}`)"
    origin = f" — [original thread]({thread_url})" if thread_url else ""
    return (
        f"> ↩ **regression** · first raised round {record.since_round}, "
        f"{fixed}{origin}"
    )


def apply_lifecycle_block(*, body: str, block: str, historical: bool) -> str:
    """Stamp a lifecycle block onto an existing inline comment body.

    Args:
        body: The comment's current body.
        block: Rendered lifecycle block.
        historical: Whether the comment's agent-prompt panel should be retitled
            ``(historical)``. Only a settled finding earns that: a partially
            addressed pattern still wants a live prompt.

    Returns:
        The new body. An existing block is replaced rather than appended to, so
        repeated rounds never stack banners.
    """
    stamped = _retitle_prompt_panel(body=body) if historical else body
    if _LIFECYCLE_BLOCK_RE.search(stamped):
        return _LIFECYCLE_BLOCK_RE.sub(lambda _match: block, stamped, count=1)
    return f"{stamped.rstrip()}\n\n{block}"


def _retitle_prompt_panel(*, body: str) -> str:
    """Mark the comment's agent-prompt panel as historical, once.

    Args:
        body: The comment's current body.

    Returns:
        The body with ``(historical)`` appended to the panel title. A title
        that already carries the suffix is left alone, so the edit is
        idempotent across rounds.
    """

    def _rewrite(match: re.Match[str]) -> str:
        title = match.group("title")
        if title.endswith(HISTORICAL_SUFFIX.strip()) or HISTORICAL_SUFFIX in title:
            return match.group(0)
        return f"> ⚡ **{title}{HISTORICAL_SUFFIX}**"

    return _PANEL_TITLE_RE.sub(_rewrite, body)
