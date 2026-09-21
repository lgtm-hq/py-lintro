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

__all__ = [
    "apply_verification_verdicts",
    "cited_paths",
    "cites_finding",
    "parse_verification_answer",
]

#: A quoted citation: ``"path:line"``, ``` `path:line` ``` or ``(path:line)``.
#: The only way to cite a path that contains a space.
#: One pattern per delimiter pair, each excluding only its own closing
#: character, so a path may contain any other: ``"dir (legacy)/api.py:12"``.
_QUOTED_CITATIONS = (
    re.compile(r'"([^"]+?):(\d+(?:-\d+)?)"'),
    re.compile(r"`([^`]+?):(\d+(?:-\d+)?)`"),
    re.compile(r"\(([^()]+?):(\d+(?:-\d+)?)\)"),
)
#: A bare citation token, after wrapping punctuation is stripped. Both
#: forms take a line or a ``start-end`` range.
_BARE_CITATION = re.compile(r"^(.+?):(\d+(?:-\d+)?)$")
#: Sentence punctuation a bare token may trail; wrappers are stripped only as
#: a matching pair (``"…"``, ``` `…` ```, ``(…)``, ``[…]``), never a lone
#: trailing ``)`` that may belong to the path.
_TRAILING = ",;."
_PAIRS = (('"', '"'), ("`", "`"), ("(", ")"), ("[", "]"), ("'", "'"))


def _unwrap(token: str) -> str:
    """Strip one matching wrapper pair from a token, if it has one.

    Args:
        token: A whitespace-delimited token of the evidence.

    Returns:
        The token without its wrapper when it both starts and ends with a
        matching pair; otherwise unchanged.
    """
    for opening, closing in _PAIRS:
        if len(token) > 2 and token.startswith(opening) and token.endswith(closing):
            return token[1:-1]
    return token


def cited_paths(*, evidence: str) -> tuple[str, ...]:
    """Return the normalized paths the evidence cites as ``path:line``.

    Citations are whitespace-delimited tokens (the prompt's ``file:line —
    what you found`` shape) with trailing sentence punctuation and one
    matching wrapper pair stripped, or a quoted ``"path:line"`` when the
    path contains a space. No pattern is built from the path, so nothing
    model-authored reaches a regex.

    Args:
        evidence: The verifier's evidence text.

    Returns:
        The cited paths, normalized, in order of appearance.
    """
    text = evidence.replace("\\", "/")
    paths = [
        normalize_file_path(m.group(1))
        for pattern in _QUOTED_CITATIONS
        for m in pattern.finditer(text)
    ]
    for token in text.split():
        match = _BARE_CITATION.match(_unwrap(token.rstrip(_TRAILING)))
        if match:
            paths.append(normalize_file_path(match.group(1)))
    return tuple(path for path in paths if path)


def cites_finding(*, evidence: str, file: str) -> bool:
    """Return whether the evidence cites a ``file:line`` in the finding's file.

    The verifier was shown one finding's cited code; a citation into any
    other path is not evidence from the material it was given, so it
    cannot refute the finding (#2734 review).

    Args:
        evidence: The verifier's evidence text.
        file: The finding's repository-relative path.

    Returns:
        True when a cited path equals ``file`` exactly, both normalized
        (separators, a leading ``./``, stray whitespace). The prompt shows
        the verifier the full path, so a bare file name, a trailing
        fragment or a longer path (``foo:pkg/api.py``, ``../pkg/api.py``)
        never counts. An empty ``file`` never matches.
    """
    target = normalize_file_path(file)
    return bool(target) and target in cited_paths(evidence=evidence)


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
        if outcome is not VerificationOutcome.CONFIRMED and not cited_paths(
            evidence=evidence,
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
