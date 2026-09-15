"""The narrative half of the synthesis envelope (lintro-ops milestone 0).

Chunks report findings only, so the round's ``summary``, its
``verdict_reasoning`` and the ``duplicates`` it merges are written once by the
synthesis pass. This module reads that envelope and applies the duplicate
merges deterministically; the cross-file findings half of the same envelope is
read by :mod:`lintro.ai.review.synthesis_response`, exactly as before.

Every parser degrades rather than raises: a missing or malformed narrative
field leaves that field ``None``/empty and never turns a completed review into
a failed one. Duplicate merges are applied only when every reference resolves
to a merged finding, and a merge never drops the more severe side.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from loguru import logger

from lintro.ai.json_response import strip_json_fences
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.models.verdict_reasoning import VerdictReasoning
from lintro.ai.review.narrative_parser import parse_narrative

__all__ = [
    "DuplicateGroup",
    "FindingKey",
    "SynthesisNarrative",
    "apply_duplicate_groups",
    "finding_ids",
    "parse_synthesis_envelope",
    "parse_duplicate_groups",
]

#: The identity :func:`~lintro.ai.review.merge.merge_findings` keeps findings
#: distinct by, so a digest id names exactly one merged finding.
FindingKey = tuple[str, int, str]

#: A digest finding id: ``F`` followed by the finding's one-based position in
#: the merged list, as printed in the synthesis digest.
_FINDING_ID = re.compile(r"^F(?P<index>[1-9][0-9]*)$")

#: Severity rank used to decide which side of a duplicate survives.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.P1: 3,
    Severity.P2: 2,
    Severity.P3: 1,
}


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """One duplicate merge the synthesis pass proposed.

    Attributes:
        keep: Digest id (``F3``) of the finding to keep; a ``file:line`` is
            accepted only when it names exactly one merged finding.
        drop: References, in the same form, of the findings that restate it.
    """

    keep: str
    drop: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SynthesisNarrative:
    """What the synthesis envelope carried besides its cross-file findings.

    Attributes:
        summary: The round's headline and walkthrough, or ``None``.
        verdict_reasoning: The round's verdict explanation, or ``None``.
        duplicates: Duplicate merges the pass proposed, in reported order.
        payload: The parsed envelope, or ``None`` when the response was not a
            JSON object. The findings half is read from the same payload by
            the findings parser so the response is decoded once.
    """

    summary: ReviewSummary | None = None
    verdict_reasoning: VerdictReasoning | None = None
    duplicates: tuple[DuplicateGroup, ...] = field(default_factory=tuple)
    payload: dict[str, Any] | None = None


def parse_synthesis_envelope(*, content: str) -> SynthesisNarrative:
    """Read the narrative fields of a synthesis response.

    Args:
        content: Raw model response text.

    Returns:
        The narrative. Every field is ``None``/empty when the response could
        not be decoded; the caller still decides pass failure from the
        findings half, so an unreadable envelope fails the pass exactly as it
        did before the narrative existed.
    """
    try:
        payload = json.loads(strip_json_fences(content=content))
    except (json.JSONDecodeError, ValueError):
        return SynthesisNarrative()
    if not isinstance(payload, dict):
        return SynthesisNarrative()
    summary, verdict_reasoning = parse_narrative(payload=payload)
    return SynthesisNarrative(
        summary=summary,
        verdict_reasoning=verdict_reasoning,
        duplicates=parse_duplicate_groups(raw_duplicates=payload.get("duplicates")),
        payload=payload,
    )


def parse_duplicate_groups(*, raw_duplicates: object) -> tuple[DuplicateGroup, ...]:
    """Parse the ``duplicates`` list of a synthesis payload.

    Args:
        raw_duplicates: Raw ``duplicates`` value from the parsed payload.

    Returns:
        Well-formed groups in payload order; a group with no ``keep`` or no
        usable ``drop`` entry is skipped.
    """
    if not isinstance(raw_duplicates, list):
        return ()
    groups: list[DuplicateGroup] = []
    for item in raw_duplicates:
        if not isinstance(item, dict):
            continue
        keep = _as_ref(item.get("keep"))
        raw_drop = item.get("drop")
        drop = (
            tuple(
                ref
                for ref in (_as_ref(value) for value in raw_drop)
                if ref and ref != keep
            )
            if isinstance(raw_drop, list)
            else ()
        )
        if not keep or not drop:
            continue
        groups.append(DuplicateGroup(keep=keep, drop=drop))
    return tuple(groups)


def _as_ref(value: object) -> str:
    """Return a stripped finding reference, or an empty string."""
    return value.strip() if isinstance(value, str) else ""


def finding_ids(*, findings: Sequence[ReviewFinding]) -> dict[FindingKey, str]:
    """Assign every merged finding the digest id the synthesis pass refers by.

    ``file:line`` does not identify a finding: two findings at one location
    with different titles both survive :func:`~lintro.ai.review.merge.merge_findings`.
    The digest therefore prints ``F<n>``, the finding's one-based position in
    the merged list, and duplicate groups name that id.

    Args:
        findings: The merged chunk findings, in reported order.

    Returns:
        Mapping from each finding's merge key ``(file, line, title)`` to its
        id. Questions are keyed too, so a digest that omits them still
        numbers the findings around them consistently. When two findings
        share a key (a custom-agent finding restating a chunk finding), the
        earliest keeps the id: the digest then prints one id for that key,
        and a reference to it resolves to the same, earliest, finding.
    """
    ids: dict[FindingKey, str] = {}
    for index, finding in enumerate(findings, start=1):
        ids.setdefault((finding.file, finding.line, finding.title), f"F{index}")
    return ids


def _resolve_reference(
    *,
    ref: str,
    findings: Sequence[ReviewFinding],
    by_label: Mapping[str, tuple[ReviewFinding, ...]],
) -> ReviewFinding | None:
    """Return the merged finding a duplicate reference names, or ``None``.

    A digest id resolves by position. A ``file:line`` resolves only when
    exactly one merged finding occurs there; an ambiguous location cannot
    say which finding the model meant, so it resolves to nothing.

    Args:
        ref: The ``keep`` or ``drop`` reference as the model wrote it.
        findings: The merged chunk findings, in reported order.
        by_label: Findings occurring at each ``file:line`` label.

    Returns:
        The referenced finding, or ``None`` when unresolved or ambiguous.
    """
    match = _FINDING_ID.match(ref)
    if match is not None:
        index = int(match.group("index"))
        return findings[index - 1] if index <= len(findings) else None
    candidates = by_label.get(ref, ())
    return candidates[0] if len(candidates) == 1 else None


def apply_duplicate_groups(
    *,
    findings: Sequence[ReviewFinding],
    groups: Sequence[DuplicateGroup],
) -> tuple[tuple[ReviewFinding, ...], int]:
    """Collapse duplicate findings the synthesis pass pointed out.

    A group is applied only when its ``keep`` and every ``drop`` reference
    resolve to a merged finding — by digest id, or by a ``file:line`` that
    exactly one finding occurs at — and every member is the same kind: a
    question never merges with a finding in either direction, because
    questions carry no verdict weight. The model's choice of which side to
    keep is advisory: the highest severity in the group survives, and among
    equals the earliest reported; the dropped sites are folded into the
    survivor's ``occurrences`` so no location disappears from the report.

    Args:
        findings: The merged chunk findings, in reported order.
        groups: Duplicate groups the pass proposed.

    Returns:
        Tuple of ``(surviving findings, number of findings dropped)``.
    """
    dropped, absorbed = _plan_duplicate_drops(findings=findings, groups=groups)
    if not dropped:
        return tuple(findings), 0
    kept: list[ReviewFinding] = []
    for finding in findings:
        if id(finding) in dropped:
            continue
        extra = absorbed.get(id(finding))
        if not extra:
            kept.append(finding)
            continue
        known = {occurrence.label for occurrence in finding.all_occurrences}
        added: list[FindingOccurrence] = []
        for occurrence in extra:
            if occurrence.label in known:
                continue
            known.add(occurrence.label)
            added.append(occurrence)
        kept.append(
            replace(finding, occurrences=(*finding.all_occurrences, *added)),
        )
    logger.info(
        "Synthesis merged {n} duplicate finding(s) into their root causes.",
        n=len(dropped),
    )
    return tuple(kept), len(dropped)


def _plan_duplicate_drops(
    *,
    findings: Sequence[ReviewFinding],
    groups: Sequence[DuplicateGroup],
) -> tuple[dict[int, ReviewFinding], dict[int, list[FindingOccurrence]]]:
    """Decide which findings each duplicate group drops and who absorbs them.

    Groups may overlap or chain (``B`` dropped into ``A`` by one group, then
    ``A`` named as a drop of ``C`` by the next). Every reference is resolved
    to its *current* survivor before a group is applied, so a finding that an
    earlier group already dropped can neither survive a later group nor take
    its members down with it: what it absorbed moves to the new survivor.

    Args:
        findings: The merged chunk findings, in reported order.
        groups: Duplicate groups the pass proposed.

    Returns:
        Tuple of ``(dropped, absorbed)``: findings to drop keyed by identity,
        and the occurrences each surviving finding absorbs, keyed the same way.
    """
    order = {id(finding): index for index, finding in enumerate(findings)}
    labelled: dict[str, list[ReviewFinding]] = {}
    for finding in findings:
        for occurrence in finding.all_occurrences:
            holders = labelled.setdefault(occurrence.label, [])
            if not any(holder is finding for holder in holders):
                holders.append(finding)
    by_label = {label: tuple(holders) for label, holders in labelled.items()}
    dropped: dict[int, ReviewFinding] = {}
    absorbed: dict[int, list[FindingOccurrence]] = {}
    survivor_of: dict[int, ReviewFinding] = {}

    def live(finding: ReviewFinding) -> ReviewFinding:
        while id(finding) in survivor_of:
            finding = survivor_of[id(finding)]
        return finding

    for group in groups:
        refs = (group.keep, *group.drop)
        members = [
            _resolve_reference(ref=ref, findings=findings, by_label=by_label)
            for ref in refs
        ]
        if any(member is None for member in members):
            logger.debug(
                "Ignoring synthesis duplicate group {refs}: unresolved or "
                "ambiguous reference.",
                refs=refs,
            )
            continue
        resolved = {
            id(current): current
            for current in (live(member) for member in members if member is not None)
        }
        if len(resolved) < 2:
            continue
        if len({member.kind for member in resolved.values()}) > 1:
            logger.debug(
                "Ignoring synthesis duplicate group {refs}: a question and a "
                "finding never merge.",
                refs=refs,
            )
            continue
        survivor = min(
            resolved.values(),
            key=lambda item: (-_SEVERITY_RANK[item.severity], order[id(item)]),
        )
        for member in resolved.values():
            if member is survivor:
                continue
            dropped[id(member)] = member
            survivor_of[id(member)] = survivor
            absorbed.setdefault(id(survivor), []).extend(member.all_occurrences)
            absorbed[id(survivor)].extend(absorbed.pop(id(member), []))
    return dropped, absorbed
