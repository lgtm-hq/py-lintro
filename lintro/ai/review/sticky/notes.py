"""Notes-block renderer for the sticky board (#2572).

The posting policy clears ``posted_inline`` on a finding below the inline
confidence floor and, unless questions are configured to post inline, on every
question. Those never open a thread: they are collected here instead, in a
collapsed block that feeds neither the verdict nor the open-findings table.

The block lives in its own module rather than beside the other section
renderers because it is the one block whose contents are decided by the
posting policy, and it needs the round's tracked records to explain why a
thread it did not re-open is still open.
"""

from __future__ import annotations

from lintro.ai.review.finding_matcher import fingerprint_for
from lintro.ai.review.github_constants import _SEVERITY_EMOJI
from lintro.ai.review.lifecycle.markers import file_line_url
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.posting_policy import (
    PostingPolicy,
    describe_notes,
    note_findings,
)
from lintro.ai.review.sticky.cells import _cell, _inline_safe, _plural
from lintro.ai.review.sticky.constants import _QUESTION_EMOJI

__all__ = ["_notes_section"]

#: Tag on a note that kept an earlier round's inline thread open: the record
#: was carried rather than stamped fixed, and the reader should know why the
#: thread stays. The wording names the routing reason, which differs by kind —
#: a question is never below the floor, it is simply not posted inline.
_CARRIED_FINDING_TAG = "(below the inline confidence floor this round)"
_CARRIED_QUESTION_TAG = "(questions are not posted inline)"


def _carried_tag(*, finding: ReviewFinding) -> str:
    """Name the reason this note did not re-open its still-open thread.

    Args:
        finding: The note being rendered.

    Returns:
        The parenthetical tag for the finding's kind.
    """
    if finding.is_question:
        return _CARRIED_QUESTION_TAG
    return _CARRIED_FINDING_TAG


def _notes_pruning_marker(*, dropped: int) -> str:
    """Return the marker naming notes the size budget dropped.

    The notes block shrinks under the same pressure as the open-findings table
    rather than being left to the body's last-resort tail truncation, which
    would silently cut the fix-all prompt and the footer below it (#2418).

    Args:
        dropped: Notes not rendered.

    Returns:
        str: The blockquote marker line.
    """
    return (
        f"> ✂️ **{dropped} more "
        f"{_plural(count=dropped, noun='note')} not listed** to fit "
        "GitHub's size limit — see the workflow run log."
    )


def _notes_caption(*, policy: PostingPolicy) -> str:
    """Describe the routing rule the notes block was rendered under.

    Args:
        policy: Posting policy applied to this round's findings.

    Returns:
        The ``<sub>`` caption naming the confidence floor and, when questions
        are routed here too, saying so.
    """
    caption = (
        "Not posted as threads and not counted in the verdict: findings below "
        f"the inline confidence floor ({policy.inline_min_confidence})"
    )
    if not policy.post_questions_inline:
        caption += " and open questions"
    return f"<sub>{caption}.</sub>"


def _note_line(
    *,
    finding: ReviewFinding,
    repo: str,
    head_sha: str,
    carried: bool = False,
) -> str:
    """Render one notes-block entry with its ``file:line`` link.

    Args:
        finding: Finding the posting policy routed to the notes block.
        repo: ``owner/name`` slug used to build the file link.
        head_sha: Commit the link pins, so it survives later pushes.
        carried: True when this note was paired with a prior open record whose
            inline thread was posted, so the entry is tagged with the reason
            that thread was not resolved.

    Returns:
        A Markdown list item: kind or severity, title, linked location, and
        the finding's description on one line.
    """
    if finding.is_question:
        label = f"{_QUESTION_EMOJI} question"
    else:
        label = f"{_SEVERITY_EMOJI[finding.severity]} {finding.severity.value}"
    confidence = _cell(text=finding.confidence, limit=20)
    if confidence and not finding.is_question:
        label = f"{label} · {confidence} confidence"
    title = _inline_safe(text=finding.title, limit=200)
    path = _cell(text=finding.file or "(unknown)", limit=200)
    location = f"{path}:{finding.line}" if finding.line > 0 else path
    # A path the sanitizer had to alter (a ``|``, a newline, a mention, or a
    # truncation) no longer names the file, so it is rendered as text rather
    # than linked to a location that does not exist. Characters CommonMark
    # would choke on are percent-encoded by ``file_line_url`` instead.
    url = (
        file_line_url(repo=repo, sha=head_sha, path=path, line=finding.line)
        if path == finding.file
        else ""
    )
    where = f"[`{location}`]({url})" if url else f"`{location}`"
    line = f"- {label} — **{title}** · {where}"
    if carried:
        line = f"{line} {_carried_tag(finding=finding)}"
    # Collapsed the way ``_cell`` flattens a table cell: the description is a
    # continuation of one list item, so an internal newline would drop the rest
    # of it out of the list and render it at column 0.
    description = (
        _inline_safe(text=finding.description, limit=600)
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )
    if description:
        line = f"{line}\n  {description}"
    return line


def _notes_section(
    *,
    result: ReviewResult,
    repo: str,
    head_sha: str,
    carries: frozenset[tuple[str, int]] = frozenset(),
    policy: PostingPolicy | None = None,
    limit: int | None = None,
) -> str:
    """Render the collapsed block for findings not posted inline (#2572).

    A low-confidence finding or a question never opens a thread: the posting
    policy routes it here instead, where the author can read it without
    having to resolve anything. Nothing in this block feeds the verdict, the
    tiles, or the open-findings table. A note that kept an earlier round's
    inline thread open is tagged, because that thread was carried rather than
    resolved on its account.

    Args:
        result: Current review result, with ``posted_inline`` already set on
            each finding by the posting policy.
        repo: ``owner/name`` slug used to build the file links.
        head_sha: Commit the links pin.
        carries: ``(fingerprint, line)`` of each note the round's matching
            paired with a prior open record whose thread was posted.
        policy: Posting policy the flags were set under; the default policy
            when the caller did not carry one.
        limit: Maximum number of notes to list. Shares the open-finding limit
            so this section shrinks under the same size pressure instead of
            being left to blunt tail truncation.

    Returns:
        A ``<details>`` block titled ``Notes and questions (N)``, or an empty
        string when every finding was posted inline.
    """
    summary = describe_notes(findings=result.findings)
    if not summary:
        return ""
    notes = note_findings(findings=result.findings)
    shown = notes if limit is None else notes[:limit]
    lines = [
        f"<details><summary>💬 {summary}</summary>",
        "",
        _notes_caption(policy=policy or PostingPolicy()),
        "",
        *(
            _note_line(
                finding=finding,
                repo=repo,
                head_sha=head_sha,
                carried=(
                    fingerprint_for(
                        file=finding.file,
                        category=finding.category,
                        title=finding.title,
                    ),
                    finding.line,
                )
                in carries,
            )
            for finding in shown
        ),
    ]
    dropped = len(notes) - len(shown)
    if dropped > 0:
        lines.extend(["", _notes_pruning_marker(dropped=dropped)])
    lines.extend(["", "</details>"])
    return "\n".join(lines)
