"""Answer parsing for the verification pass (#2728).

The verifier answers one JSON object keyed by finding position; an unknown
outcome label or an out-of-range position is skipped so a malformed entry
costs one finding its verdict, not the round its pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.json_response import strip_json_fences
from lintro.ai.review.enums.severity_downgrade_reason import SeverityDowngradeReason
from lintro.ai.review.enums.verification_outcome import VerificationOutcome
from lintro.ai.review.finding_identity import normalize_file_path
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.verification_outcome import RefutedFinding

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["apply_verification_verdicts", "cites_finding", "parse_verification_answer"]

#: A ``path:line`` citation somewhere in the evidence text.
_CITATION = re.compile(r"([\w./\\-]+\.\w+):\d+")


def cites_finding(*, evidence: str, file: str) -> bool:
    """Return whether the evidence cites a ``file:line`` in the finding's file.

    The verifier was shown one finding's cited code; a citation into any
    other path is not evidence from the material it was given, so it
    cannot refute the finding (#2734 review).

    Args:
        evidence: The verifier's evidence text.
        file: The finding's repository-relative path.

    Returns:
        True when some citation names exactly ``file`` (both sides
        normalized: separators, a leading ``./``, stray whitespace). The
        prompt shows the verifier the full path, so a bare file name or a
        trailing fragment is not accepted: with two findings on files of
        the same name it could not tell them apart. An empty ``file`` never
        matches.
    """
    target = normalize_file_path(file)
    if not target:
        return False
    # Look for the path itself rather than tokenizing the evidence: a path
    # with a space in it would otherwise be cut at the space.
    haystack = evidence.replace("\\", "/")
    # Left boundary: nothing that could be part of a longer path — no word
    # character, slash, dot or dash — so ``foo-pkg/api.py:2``, ``x.pkg/…``
    # and ``../pkg/…`` cannot cite ``pkg/api.py``; a leading ``./`` may.
    pattern = r"(?:^|[^\w/.-])(?:\./)?" + re.escape(target) + r":\d+(?!\d)"
    return re.search(pattern, haystack) is not None


def parse_verification_answer(
    *,
    content: str,
    count: int,
) -> dict[int, tuple[VerificationOutcome, str]] | None:
    """Parse the verifier's answer into ``position -> (outcome, evidence)``.

    Args:
        content: The raw answer text.
        count: How many findings were put to the verifier.

    Returns:
        The parsed verdicts keyed by 1-based position, or ``None`` when the
        answer is not the JSON shape asked for. An unknown outcome label or
        an out-of-range position is skipped; a finding with no verdict stays
        unverified.
    """
    try:
        payload = json.loads(strip_json_fences(content=content))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    items = payload.get("verifications")
    if not isinstance(items, list):
        return None
    verdicts: dict[int, tuple[VerificationOutcome, str]] = {}
    labels = {
        "refuted": VerificationOutcome.REFUTED,
        "weakened": VerificationOutcome.DOWNGRADED,
        "unrefuted": VerificationOutcome.CONFIRMED,
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        position = item.get("index")
        outcome = labels.get(str(item.get("outcome", "")).strip().lower())
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or not 1 <= position <= count
            or outcome is None
        ):
            continue
        evidence = item.get("evidence")
        evidence = evidence.strip() if isinstance(evidence, str) else ""
        if outcome is not VerificationOutcome.CONFIRMED and not _CITATION.search(
            evidence,
        ):
            # The prompt's first rule: a refutation — or a weakening, which
            # lowers a blocker — without a ``file:line`` citation is no
            # evidence. Emptying it makes the application keep the finding
            # as confirmed.
            evidence = ""
        verdicts.setdefault(position, (outcome, evidence))
    return verdicts


def apply_verification_verdicts(
    *,
    findings: Sequence[ReviewFinding],
    indices: Sequence[int],
    verdicts: dict[int, tuple[VerificationOutcome, str]],
) -> tuple[tuple[ReviewFinding, ...], int, int, int, tuple[RefutedFinding, ...]]:
    """Rewrite the round's findings with the verifier's verdicts.

    A refutation without evidence is not a refutation (the prompt's first
    rule), so it is kept as confirmed rather than dropping a finding on the
    verifier's word alone; a downgrade only applies to a P1.

    Args:
        findings: The round's findings.
        indices: Which of them were verified, in prompt order.
        verdicts: The parsed verdicts by 1-based position.

    Returns:
        The rewritten findings, the confirmed / refuted / downgraded counts,
        and the refutation records.
    """
    rewritten = list(findings)
    drop: set[int] = set()
    confirmed = refuted = downgraded = 0
    refutations: list[RefutedFinding] = []
    for position, index in enumerate(indices, start=1):
        verdict = verdicts.get(position)
        if verdict is None:
            continue
        outcome, evidence = verdict
        finding = findings[index]
        if outcome is VerificationOutcome.REFUTED and not cites_finding(
            evidence=evidence,
            file=finding.file,
        ):
            # A citation into some other file is not evidence from the
            # material the verifier was shown; the finding stands.
            logger.info(
                "Verification refuted {title!r} without citing {file}; kept.",
                title=finding.title,
                file=finding.file,
            )
            evidence = ""
        if outcome is VerificationOutcome.REFUTED and evidence:
            drop.add(index)
            refuted += 1
            refutations.append(
                RefutedFinding(
                    file=finding.file,
                    line=finding.line,
                    severity=str(finding.severity),
                    title=finding.title,
                    evidence=evidence,
                ),
            )
            logger.info(
                "Verification refuted {title!r} at {file}:{line}: {evidence}",
                title=finding.title,
                file=finding.file,
                line=finding.line,
                evidence=evidence,
            )
            continue
        if outcome is VerificationOutcome.DOWNGRADED and not cites_finding(
            evidence=evidence,
            file=finding.file,
        ):
            # Lowering a blocker takes the same evidence as dropping one.
            logger.info(
                "Verification weakened {title!r} without citing {file}; kept.",
                title=finding.title,
                file=finding.file,
            )
            outcome = VerificationOutcome.CONFIRMED
        if outcome is VerificationOutcome.DOWNGRADED:
            if finding.severity is Severity.P1:
                logger.info(
                    "Verification weakened {title!r} at {file}:{line} to P2: {why}",
                    title=finding.title,
                    file=finding.file,
                    line=finding.line,
                    why=evidence or "no reason given",
                )
                rewritten[index] = replace(
                    finding,
                    severity=Severity.P2,
                    severity_downgraded=True,
                    severity_downgrade_reason=(
                        SeverityDowngradeReason.REFUTATION_WEAKENED
                    ),
                    verified=True,
                )
                downgraded += 1
                continue
            # The prompt scopes ``weakened`` to P1; a lower band that came
            # back weakened is kept at its severity and counts as confirmed,
            # said out loud so the answer is not silently reinterpreted.
            logger.info(
                "Verification answered 'weakened' for {severity} {title!r}; "
                "only a P1 is moved, kept as confirmed.",
                severity=str(finding.severity),
                title=finding.title,
            )
        rewritten[index] = replace(finding, verified=True)
        confirmed += 1
    kept = tuple(item for index, item in enumerate(rewritten) if index not in drop)
    return kept, confirmed, refuted, downgraded, tuple(refutations)
