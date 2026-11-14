"""Verify before resolve: one provider call per round for moved findings (#3466).

A prior open finding whose location had to be inferred (its file renamed,
moved, copied or deleted, its line unplaceable, its base moved, or its record
carrying a lineage) and that no report matched this round is no longer
resolved on that silence (:mod:`lintro.ai.review.moved_candidates`). Instead
this pass asks the provider once per round, for all of them together,
whether each original issue still exists anywhere in the changed files:

* ``still_present: false`` resolves the finding ("verified fixed");
* ``true`` keeps it open, moved to the location the answer names when that
  line is inside the pull request's diff and no finding reported this round
  already claims it there; otherwise it is held as "still present";
* ``unknown``, a provider error or timeout, an exhausted budget, an
  interrupt, an off-schema answer, or ``review.verify_moved_findings: false``
  hold it as "not re-verified". Nothing on this path ever resolves.

The call runs on the completed path after the verification pass
(:mod:`lintro.ai.review.verification`), shares its provider seam, its
interrupt handling and the run's cost budget, and is fail-soft.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.cli_schemas import cli_schema_for_moved_verification
from lintro.ai.exceptions import AITurnLimitError
from lintro.ai.prompts.review import (
    REVIEW_MOVED_VERIFICATION_SYSTEM_PROMPT,
    REVIEW_MOVED_VERIFICATION_USER_PROMPT_TEMPLATE,
)
from lintro.ai.review import provider_call
from lintro.ai.review.diff_gate import hunks_from_diff
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.moved_verdict import MovedVerdict
from lintro.ai.review.finding_identity import fingerprint_for
from lintro.ai.review.models.moved_verification import MovedVerificationSummary
from lintro.ai.review.moved_verification_prompt import (
    Material,
    parse_moved_answer,
    render_candidates,
    render_material,
)
from lintro.ai.review.prompt_redaction import redact_prompt_text
from lintro.ai.review.verification import (
    VerificationInterruptedError,
    await_call_until_stop,
)
from lintro.ai.sanitize import make_boundary_marker

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence

    from lintro.ai.budget import CostBudget
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.moved_verification import MovedVerdictEntry
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "MAX_MOVED_FINDINGS",
    "MovedVerification",
    "MovedVerificationRequest",
    "run_moved_verification",
]

#: Candidates put to the provider in one call; the rest are held.
MAX_MOVED_FINDINGS = 20

#: Completion budget for the answer: one short entry per finding.
_MAX_TOKENS = 4_096


@dataclass(frozen=True, slots=True, kw_only=True)
class MovedVerificationRequest:
    """Everything the moved-finding check reads.

    Attributes:
        context: The round's context: head, changed files and diff.
        candidates: Prior open records to check, at their stored locations
            (:func:`lintro.ai.review.moved_candidates.select_candidates`).
        findings: The round's findings; a location one of them already
            claims is never handed to a candidate.
        enabled: ``review.verify_moved_findings``; off holds every candidate
            without a call.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget and timeouts.
        budget: Session cost budget the call counts against.
        repo_root: The repository under review.
        content_budget_tokens: Tokens the diff and file content may use; with
            none the check holds every candidate without a call, and when
            the material does not fit a "fixed" answer is held as unknown.
        no_tools: When True the call goes out without tools.
        stop: The run's interrupt event.
    """

    context: ReviewContext
    candidates: Sequence[FindingRecord]
    findings: Sequence[ReviewFinding]
    enabled: bool
    provider: BaseAIProvider
    ai_config: AIConfig
    budget: CostBudget
    repo_root: str = ""
    content_budget_tokens: int = 0
    no_tools: bool = False
    stop: asyncio.Event | None = None


@dataclass(frozen=True, slots=True)
class MovedVerification:
    """What the check decided.

    Attributes:
        verdicts: ``(key, verdict, path, line, line_sha)`` per candidate.
        summary: The counts, or ``None`` when there was no candidate.
    """

    verdicts: tuple[MovedVerdictEntry, ...] = ()
    summary: MovedVerificationSummary | None = None


def _deleted_paths(*, context: ReviewContext) -> frozenset[str]:
    """Return the paths the pull request deletes.

    Args:
        context: The round's context.

    Returns:
        The deleted paths.
    """
    return frozenset(
        item.path
        for item in context.changed_files
        if item.status is ChangedFileStatus.DELETED
    )


def _held(
    *,
    request: MovedVerificationRequest,
    candidates: Sequence[FindingRecord],
    failed: bool = False,
    unverifiable: bool = False,
    usage: tuple[int, int, float] = (0, 0, 0.0),
) -> MovedVerification:
    """Hold every candidate as not re-verified.

    Args:
        request: The check's inputs.
        candidates: The candidates.
        failed: Whether a call was made and gave no usable answer.
        unverifiable: Whether the material could not be shown whole.
        usage: Tokens and cost a failed call still consumed.

    Returns:
        The all-held result.
    """
    deleted = _deleted_paths(context=request.context)
    return MovedVerification(
        verdicts=tuple(
            (record.key, MovedVerdict.UNKNOWN.value, "", 0, "") for record in candidates
        ),
        summary=MovedVerificationSummary(
            enabled=request.enabled,
            candidates=len(candidates),
            held=len(candidates),
            held_deleted=sum(1 for record in candidates if record.file in deleted),
            failed=failed,
            incomplete=unverifiable,
            input_tokens=usage[0],
            output_tokens=usage[1],
            cost_estimate=usage[2],
        ),
    )


def _location(
    *,
    record: FindingRecord,
    path: str,
    line: int,
    request: MovedVerificationRequest,
) -> tuple[str, int, str] | None:
    """Return the location a ``present`` answer may move a record to.

    Args:
        record: The candidate.
        path: The file the answer named.
        line: The line the answer named.
        request: The check's inputs.

    Returns:
        ``(path, line, head)`` when the line is inside one of the pull
        request's hunks and no finding reported this round already sits
        there under the record's identity, else ``None``.
    """
    head = request.context.head_ref
    if not path or line <= 0 or not head:
        return None
    hunks = hunks_from_diff(diff=request.context.unified_diff).get(path)
    if hunks is None or not hunks.contains(line):
        return None
    claimed = fingerprint_for(file=path, category=record.category, title=record.title)
    taken = {
        fingerprint_for(file=item.file, category=item.category, title=item.title)
        for item in request.findings
    }
    return None if claimed in taken else (path, line, head)


def _user_prompt(
    *,
    request: MovedVerificationRequest,
    asked: int,
    material: Material,
    boundary: str,
) -> str:
    """Build the call's user prompt.

    Args:
        request: The check's inputs.
        asked: How many candidates are put to the provider.
        material: The diff and file content to show.
        boundary: The per-call fence marker the material was fenced with.

    Returns:
        The prompt.
    """
    metadata = request.context.pr_metadata
    return REVIEW_MOVED_VERIFICATION_USER_PROMPT_TEMPLATE.format(
        finding_count=asked,
        boundary=boundary,
        pr_title=redact_prompt_text(
            text=metadata.title if metadata else "Local changes",
            source="PR title",
        ),
        findings=render_candidates(
            candidates=request.candidates[:asked],
            repo_root=request.repo_root,
            boundary=boundary,
        ),
        diff=material.diff,
        files=material.files,
    )


async def run_moved_verification(
    *,
    request: MovedVerificationRequest,
) -> MovedVerification:
    """Ask the provider whether each moved finding still exists.

    Args:
        request: The check's inputs.

    Returns:
        A verdict per candidate and the round's counts. Every failure comes
        back as held, never as an exception and never as a resolve. With no
        content budget the material cannot be shown at all, so no call is
        made and every candidate is held.
    """
    candidates = tuple(request.candidates)
    if not candidates:
        return MovedVerification()
    if not request.enabled:
        return _held(request=request, candidates=candidates)
    if request.content_budget_tokens <= 0:
        logger.warning("The moved-finding check has no content budget; held.")
        return _held(request=request, candidates=candidates, unverifiable=True)
    asked = min(len(candidates), MAX_MOVED_FINDINGS)
    boundary = make_boundary_marker()
    material = render_material(
        context=request.context,
        repo_root=request.repo_root,
        boundary=boundary,
        budget_tokens=request.content_budget_tokens,
    )
    logger.info("Moved-finding check: {} findings to re-verify", asked)
    try:
        request.budget.check()
        response = await await_call_until_stop(
            call=provider_call.call_ai(
                provider=request.provider,
                ai_config=request.ai_config,
                system_prompt=REVIEW_MOVED_VERIFICATION_SYSTEM_PROMPT,
                user_prompt=_user_prompt(
                    request=request,
                    asked=asked,
                    material=material,
                    boundary=boundary,
                ),
                budget=request.budget,
                max_tokens=_MAX_TOKENS,
                repo_root=request.repo_root or None,
                use_one_shot=True,
                no_tools=request.no_tools,
                cli_schema=cli_schema_for_moved_verification(
                    transport=request.ai_config.transport,
                ),
            ),
            stop=request.stop,
        )
    except VerificationInterruptedError:
        logger.warning("The moved-finding check was interrupted; findings held.")
        return _held(request=request, candidates=candidates, failed=True)
    except AITurnLimitError as exc:
        logger.warning("The moved-finding check hit its turn limit; findings held.")
        return _held(
            request=request,
            candidates=candidates,
            failed=True,
            usage=(exc.input_tokens, exc.output_tokens, exc.cost_estimate),
        )
    except Exception:
        # Deliberately broad, like the verification pass: a budget stop, a
        # timeout or a provider error holds the findings; it never resolves
        # one and never fails the round.
        logger.opt(exception=True).warning(
            "The moved-finding check failed; findings held as not re-verified.",
        )
        return _held(request=request, candidates=candidates, failed=True)
    usage = (response.input_tokens, response.output_tokens, response.cost_estimate)
    answers = parse_moved_answer(content=response.content, count=asked)
    if not answers:
        logger.warning("The moved-finding check answered outside its schema.")
        return _held(
            request=request,
            candidates=candidates,
            failed=True,
            usage=usage,
        )
    if not material.complete:
        logger.warning(
            "The moved-finding check saw a cut or partial diff; no finding it "
            "called fixed is resolved this round.",
        )
    return _decide(
        request=request,
        candidates=candidates,
        answers=answers,
        usage=usage,
        complete=material.complete,
    )


def _decide(
    *,
    request: MovedVerificationRequest,
    candidates: tuple[FindingRecord, ...],
    answers: dict[int, tuple[MovedVerdict, str, int]],
    usage: tuple[int, int, float],
    complete: bool,
) -> MovedVerification:
    """Turn the parsed answers into verdicts and counts.

    Args:
        request: The check's inputs.
        candidates: Every candidate, the ones past the cap included.
        answers: ``position -> (verdict, file, line)``.
        usage: Tokens and cost of the call.
        complete: Whether the provider saw all of the diff and the files.
            Without that, "fixed" may only mean it did not find the issue
            in what it was shown, so it is held as unknown.

    Returns:
        The result.
    """
    deleted = _deleted_paths(context=request.context)
    entries: list[MovedVerdictEntry] = []
    fixed = present = moved = held_deleted = 0
    for position, record in enumerate(candidates, start=1):
        verdict, path, line = answers.get(position, (MovedVerdict.UNKNOWN, "", 0))
        if verdict is MovedVerdict.FIXED and not complete:
            verdict = MovedVerdict.UNKNOWN
        place = (
            _location(record=record, path=path, line=line, request=request)
            if verdict is MovedVerdict.PRESENT
            else None
        )
        fixed += verdict is MovedVerdict.FIXED
        present += verdict is MovedVerdict.PRESENT
        moved += place is not None
        held_deleted += verdict is MovedVerdict.UNKNOWN and record.file in deleted
        target = place or ("", 0, "")
        entries.append((record.key, verdict.value, *target))
    return MovedVerification(
        verdicts=tuple(entries),
        summary=MovedVerificationSummary(
            enabled=True,
            candidates=len(candidates),
            verified_fixed=fixed,
            verified_present=present,
            moved=moved,
            held=len(candidates) - fixed - present,
            held_deleted=held_deleted,
            incomplete=not complete,
            input_tokens=usage[0],
            output_tokens=usage[1],
            cost_estimate=usage[2],
        ),
    )
