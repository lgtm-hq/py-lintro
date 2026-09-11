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

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.finding_matcher import fingerprint_for
from lintro.ai.review.github_constants import _SEVERITY_EMOJI
from lintro.ai.review.lifecycle.markers import file_line_url
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.posting_policy import (
    PostingPolicy,
    describe_notes,
    note_findings,
)
from lintro.ai.review.sticky.cells import _cell, _inline_safe
from lintro.ai.review.sticky.constants import _QUESTION_EMOJI

__all__ = ["_notes_section"]

#: Tag on a note whose fingerprint is still open as an earlier round's inline
#: thread: the model re-reported it below the floor, so the record was carried
#: rather than stamped fixed, and the reader should know why the thread stays.
_CARRIED_NOTE_TAG = "(below the inline confidence floor this round)"


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
        carried: True when an earlier round's inline record for the same
            fingerprint is still open, so the entry is tagged as the reason
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
        line = f"{line} {_CARRIED_NOTE_TAG}"
    description = _inline_safe(text=finding.description, limit=600).strip()
    if description:
        line = f"{line}\n  {description}"
    return line


def _notes_section(
    *,
    result: ReviewResult,
    repo: str,
    head_sha: str,
    records: tuple[FindingRecord, ...] = (),
    policy: PostingPolicy | None = None,
) -> str:
    """Render the collapsed block for findings not posted inline (#2572).

    A low-confidence finding or a question never opens a thread: the posting
    policy routes it here instead, where the author can read it without
    having to resolve anything. Nothing in this block feeds the verdict, the
    tiles, or the open-findings table. A note whose fingerprint is still open
    from an earlier round's inline thread is tagged, because that thread was
    carried rather than resolved on its account.

    Args:
        result: Current review result, with ``posted_inline`` already set on
            each finding by the posting policy.
        repo: ``owner/name`` slug used to build the file links.
        head_sha: Commit the links pin.
        records: Tracked finding records after this round's matching, used
            to tag notes that kept a prior inline record open.
        policy: Posting policy the flags were set under; the default policy
            when the caller did not carry one.

    Returns:
        A ``<details>`` block titled ``Notes and questions (N)``, or an empty
        string when every finding was posted inline.
    """
    summary = describe_notes(findings=result.findings)
    if not summary:
        return ""
    notes = note_findings(findings=result.findings)
    open_fingerprints = {
        record.fingerprint for record in records if record.status is FindingStatus.OPEN
    }
    return "\n".join(
        [
            f"<details><summary>💬 {summary}</summary>",
            "",
            _notes_caption(policy=policy or PostingPolicy()),
            "",
            *(
                _note_line(
                    finding=finding,
                    repo=repo,
                    head_sha=head_sha,
                    carried=fingerprint_for(
                        file=finding.file,
                        category=finding.category,
                        title=finding.title,
                    )
                    in open_fingerprints,
                )
                for finding in notes
            ),
            "",
            "</details>",
        ],
    )
