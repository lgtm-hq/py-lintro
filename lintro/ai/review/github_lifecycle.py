"""Addressed lifecycle for inline finding threads (#1912).

An inline finding comment is posted once and then *edited* for the rest of the
PR's life, so the thread carries its own outcome instead of leaving a reader to
diff two rounds of comments by eye. Three stamps exist, all written into a
single replaceable block at the end of the comment body:

* ``✔ Addressed in <sha> · round N`` — every occurrence stopped reproducing.
  The comment's agent-prompt panel is retitled ``(historical)`` so nobody pastes
  a prompt for a fix that already landed, and the thread is resolved when
  ``review.auto_resolve`` allows it.
* ``✔ 14/20 addressed in <sha> · round N`` — partial progress on a collapsed
  pattern (#1925). The finding is still open, so the thread is **never**
  resolved and the prompt panel stays live.
* ``↩ Regressed in <sha> · round N`` — the finding came back. The old thread is
  stamped and *stays resolved*; the finding is re-raised on a fresh thread that
  carries the provenance back to here (mock-4 section 3, state D).

Every edit is idempotent: the block is rewritten wholesale and an unchanged
body is never PATCHed, so re-running a round adds no second banner.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from loguru import logger

from lintro.ai.review.enums.lifecycle_stage import LifecycleStage
from lintro.ai.review.github_constants import SHORT_SHA_LENGTH
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_thread import ReviewThread
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = [
    "FINDING_MARKER_PREFIX",
    "LIFECYCLE_CLOSE",
    "LIFECYCLE_OPEN",
    "LifecycleClient",
    "LifecycleReport",
    "apply_lifecycle_block",
    "finding_marker",
    "parse_finding_marker",
    "regression_provenance",
    "render_lifecycle_block",
    "sync_addressed_lifecycle",
]

#: Hidden marker tying an inline comment to the finding record that owns it.
#: The review-submission endpoint does not answer with the comment ids it
#: created, so the marker is how a posted comment is recognized when the PR's
#: comments are listed back.
FINDING_MARKER_PREFIX = "<!-- lintro-finding:"
FINDING_MARKER_SUFFIX = " -->"

#: Delimiters of the replaceable lifecycle block at the end of a comment body.
LIFECYCLE_OPEN = "<!-- lintro-lifecycle -->"
LIFECYCLE_CLOSE = "<!-- /lintro-lifecycle -->"

#: Suffix appended to a prompt panel's title once its finding is settled.
HISTORICAL_SUFFIX = " (historical)"

_FINDING_MARKER_RE = re.compile(
    re.escape(FINDING_MARKER_PREFIX) + r"\s*([0-9a-f]+#\d+)\s*-->",
)
_LIFECYCLE_BLOCK_RE = re.compile(
    re.escape(LIFECYCLE_OPEN) + r".*?" + re.escape(LIFECYCLE_CLOSE),
    re.DOTALL,
)
_PANEL_TITLE_RE = re.compile(r"^> ⚡ \*\*(?P<title>.+?)\*\*\s*$", re.MULTILINE)

#: Keys are hashes and ordinals, never model text, but the value is still
#: interpolated into a comment body — keep it to the shape the writer emits.
_KEY_RE = re.compile(r"^[0-9a-f]+#\d+$")


class LifecycleClient(Protocol):
    """The GitHub operations the lifecycle needs, and nothing more.

    Structural typing keeps this module independent of the reporter class: the
    lifecycle only ever edits a comment and resolves a thread, so a caller can
    supply any object that does those two things.
    """

    def update_review_comment(self, *, comment_id: int, body: str) -> bool:
        """Edit an inline review comment in place.

        Args:
            comment_id: Comment to edit.
            body: New Markdown body.

        Returns:
            True when the edit succeeded.
        """
        ...  # pragma: no cover - structural type only

    def fetch_review_threads(self) -> dict[int, ReviewThread] | None:
        """Map each review thread's root comment id to the thread.

        Returns:
            The mapping, or ``None`` when it could not be fetched.
        """
        ...  # pragma: no cover - structural type only

    def resolve_review_thread(self, *, thread_id: str) -> bool:
        """Resolve a review thread.

        Args:
            thread_id: GraphQL node id of the thread.

        Returns:
            True when GitHub reports the thread as resolved.
        """
        ...  # pragma: no cover - structural type only


@dataclass(frozen=True, slots=True)
class LifecycleReport:
    """Outcome of one lifecycle synchronization pass.

    Attributes:
        edited: Keys of findings whose inline comment body was rewritten.
        unchanged: Keys whose comment already carried the exact stamp, so no
            request was made.
        resolved: Keys whose review thread was resolved this pass.
        failed: Keys whose comment edit or thread resolution did not take
            effect. The banner is retried on the next round because the stored
            body still differs from the rendered one — a failed stamp degrades
            to "not stamped yet", never to a crash.
    """

    edited: tuple[str, ...] = field(default_factory=tuple)
    unchanged: tuple[str, ...] = field(default_factory=tuple)
    resolved: tuple[str, ...] = field(default_factory=tuple)
    failed: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_failures(self) -> bool:
        """Return True when any stamp or resolution did not take effect."""
        return bool(self.failed)


def finding_marker(*, key: str) -> str:
    """Render the hidden marker identifying a finding's inline comment.

    Args:
        key: The finding record's ``fingerprint#ordinal`` identity key.

    Returns:
        The HTML comment marker, or an empty string when the key is not a
        well-formed identity key.
    """
    if not _KEY_RE.match(key):
        return ""
    return f"{FINDING_MARKER_PREFIX}{key}{FINDING_MARKER_SUFFIX}"


def parse_finding_marker(*, body: str) -> str:
    """Extract the finding key a comment body is marked with.

    Args:
        body: Inline comment body as returned by the API.

    Returns:
        The identity key, or an empty string when the body carries no marker
        (a human's reply, or a comment from another tool).
    """
    match = _FINDING_MARKER_RE.search(body)
    return match.group(1) if match else ""


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


@dataclass(frozen=True, slots=True)
class _LifecycleEdit:
    """One planned comment edit.

    Attributes:
        record: Finding record being stamped.
        stage: Lifecycle stage driving the stamp.
        historical: Whether the prompt panel is retitled.
        resolvable: Whether the thread may be resolved when config allows it.
        new_thread_url: URL of the regression's fresh thread, when known.
    """

    record: FindingRecord
    stage: LifecycleStage
    historical: bool
    resolvable: bool
    new_thread_url: str = ""


def sync_addressed_lifecycle(
    *,
    reporter: LifecycleClient,
    resolved: Sequence[FindingRecord] = (),
    partial: Sequence[FindingRecord] = (),
    regressed: Sequence[FindingRecord] = (),
    comment_bodies: Mapping[int, str],
    head_sha: str,
    round_number: int,
    auto_resolve: bool = True,
    new_thread_urls: Mapping[str, str] | None = None,
) -> LifecycleReport:
    """Stamp — and optionally resolve — the threads this round settled.

    Args:
        reporter: GitHub reporter used to edit comments and resolve threads.
        resolved: Records the matcher resolved this round.
        partial: Open records of collapsed patterns with some occurrences gone.
        regressed: Records that came back this round; their *old* thread is
            stamped and deliberately left resolved.
        comment_bodies: Current bodies of the PR's inline comments, keyed by
            comment id. A record whose body is absent is skipped: overwriting
            a comment whose content is unknown would destroy the finding it
            carries.
        head_sha: Head commit sha reviewed in this round.
        round_number: 1-based round number for this run.
        auto_resolve: ``review.auto_resolve``. False keeps the banner and skips
            the GraphQL mutation entirely.
        new_thread_urls: Finding key to the URL of the fresh thread carrying
            its regression.

    Returns:
        What was edited, resolved, already current, or failed. Every failure is
        logged and reported rather than raised: a review that found real issues
        must still post, even when GitHub refuses one edit.
    """
    urls = new_thread_urls or {}
    edits = [
        *(
            _LifecycleEdit(
                record=record,
                stage=LifecycleStage.ADDRESSED,
                historical=True,
                resolvable=True,
            )
            for record in resolved
        ),
        *(
            _LifecycleEdit(
                record=record,
                stage=LifecycleStage.PARTIAL,
                historical=False,
                resolvable=False,
            )
            for record in partial
        ),
        *(
            _LifecycleEdit(
                record=record,
                stage=LifecycleStage.REGRESSED,
                historical=True,
                resolvable=False,
                new_thread_url=urls.get(record.key, ""),
            )
            for record in regressed
        ),
    ]

    edited: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    resolvable_ids: list[tuple[str, int]] = []

    for edit in edits:
        comment_id = edit.record.inline_comment_id
        if comment_id is None:
            continue
        current = comment_bodies.get(comment_id)
        if current is None:
            logger.debug(
                "No stored body for comment {} — skipping the {} banner",
                comment_id,
                edit.stage.value,
            )
            continue
        body = apply_lifecycle_block(
            body=current,
            block=render_lifecycle_block(
                record=edit.record,
                stage=edit.stage,
                head_sha=head_sha,
                round_number=round_number,
                new_thread_url=edit.new_thread_url,
            ),
            historical=edit.historical,
        )
        if body == current:
            unchanged.append(edit.record.key)
        elif reporter.update_review_comment(comment_id=comment_id, body=body):
            edited.append(edit.record.key)
        else:
            logger.warning(
                "Could not stamp the {} banner onto review comment {}; it will "
                "be retried next round",
                edit.stage.value,
                comment_id,
            )
            failed.append(edit.record.key)
            continue
        if edit.resolvable and auto_resolve:
            resolvable_ids.append((edit.record.key, comment_id))

    resolved_keys, resolve_failures = _resolve_threads(
        reporter=reporter,
        targets=resolvable_ids,
    )
    return LifecycleReport(
        edited=tuple(edited),
        unchanged=tuple(unchanged),
        resolved=tuple(resolved_keys),
        failed=(*failed, *resolve_failures),
    )


def _resolve_threads(
    *,
    reporter: LifecycleClient,
    targets: Sequence[tuple[str, int]],
) -> tuple[list[str], list[str]]:
    """Resolve the review threads rooted at the given comments.

    Args:
        reporter: GitHub reporter used for the GraphQL calls.
        targets: ``(finding key, root comment id)`` pairs to resolve.

    Returns:
        Tuple of ``(resolved keys, failed keys)``. A thread GitHub already
        reports as resolved counts as resolved without a second mutation.
    """
    if not targets:
        return [], []
    threads: dict[int, ReviewThread] | None = reporter.fetch_review_threads()
    if threads is None:
        logger.warning(
            "Could not list review threads — {} thread(s) keep their banner but "
            "stay unresolved",
            len(targets),
        )
        return [], [key for key, _ in targets]

    resolved: list[str] = []
    failed: list[str] = []
    for key, comment_id in targets:
        thread = threads.get(comment_id)
        if thread is None:
            logger.debug("No review thread found for comment {}", comment_id)
            failed.append(key)
            continue
        if thread.is_resolved:
            resolved.append(key)
            continue
        if reporter.resolve_review_thread(thread_id=thread.node_id):
            resolved.append(key)
        else:
            logger.warning("Could not resolve review thread for comment {}", comment_id)
            failed.append(key)
    return resolved, failed
