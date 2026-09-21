"""Answer parsing for the verification pass (#2728).

The verifier answers one JSON object keyed by finding position; an unknown
outcome label or an out-of-range position is skipped so a malformed entry
costs one finding its verdict, not the round its pass.
"""

from __future__ import annotations

import json
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
    "CITATION_UNPARSED",
    "apply_verification_verdicts",
    "citation_unparsed",
    "cited_paths",
    "cites_finding",
    "parse_verification_answer",
]

#: Openers that start a quoted token, each with its closer. Closed set: a
#: quoted token is the only way to cite a path that contains a space, and
#: only these three open one. ``[]`` and ``''`` are wrappers stripped from a
#: bare token, never openers.
_OPENERS = {'"': '"', "`": "`", "(": ")"}
#: Wrapper pairs a bare token may carry once around it.
_WRAPPERS = (("[", "]"), ("'", "'"))
#: Sentence punctuation a bare token may trail.
_TRAILING = ",;."
#: Prefix put on the evidence when a quoted citation was never closed, so
#: the record shows the verifier's citation could not be read.
CITATION_UNPARSED = "citation_unparsed:"


def _tokens(evidence: str) -> tuple[tuple[str, ...], bool]:
    """Split the evidence into citation candidates in one left-to-right pass.

    A token opened by ``"``, a backtick or ``(`` runs to its own closer and
    may contain anything else, spaces included; it must then be followed by
    whitespace, the end or sentence punctuation, or it and whatever is glued
    to it are one malformed token — a deliberate rule of the frozen grammar
    (#2734 ruling), the one that keeps ``"…"target.py:7`` from citing
    anything. A bare token runs to the next whitespace.
    Quoted and bare tokens come from the same walk over the same characters
    exactly once, so nothing is matched twice or rescanned, and a delimiter
    in the middle of a bare token is just one
    of its characters.

    Args:
        evidence: The verifier's evidence text, separators normalized.

    Returns:
        The tokens, and whether an opener was left unterminated (the rest
        of the text is then prose and yields no token).
    """
    tokens: list[str] = []
    i = 0
    length = len(evidence)
    while i < length:
        char = evidence[i]
        if char.isspace():
            i += 1
            continue
        closer = _OPENERS.get(char)
        if closer is not None:
            end = evidence.find(closer, i + 1)
            if end == -1:
                return tuple(tokens), True
            after = end + 1
            while after < length and evidence[after] in _TRAILING:
                after += 1
            if after < length and not evidence[after].isspace():
                # Glued to what follows: one malformed token, no citation.
                while after < length and not evidence[after].isspace():
                    after += 1
            else:
                tokens.append(evidence[i + 1 : end])
            i = after
            continue
        end = i
        while end < length and not evidence[end].isspace():
            end += 1
        token = evidence[i:end].rstrip(_TRAILING)
        for opening, closing in _WRAPPERS:
            if len(token) > 2 and token[0] == opening and token[-1] == closing:
                token = token[1:-1]
                break
        tokens.append(token)
        i = end
    return tuple(tokens), False


def _citation_path(token: str) -> str:
    """Return the normalized path a ``path:line`` token cites, or ``""``.

    Args:
        token: One token from :func:`_tokens`.

    Returns:
        The path when the token splits at its last ``:`` into a non-empty
        path and a line or ``start-end`` range; otherwise an empty string.
    """
    path, sep, line = token.rpartition(":")
    if not sep:
        return ""
    first, dash, last = line.partition("-")
    if not first.isdigit() or (dash and not last.isdigit()):
        return ""
    return normalize_file_path(path)


def cited_paths(*, evidence: str) -> tuple[str, ...]:
    """Return the normalized paths the evidence cites as ``path:line``.

    Args:
        evidence: The verifier's evidence text.

    Returns:
        The cited paths, normalized, in order of appearance. An
        unterminated quoted citation ends the scan; see
        :func:`citation_unparsed`.
    """
    tokens, _unterminated = _tokens(evidence.replace("\\", "/"))
    return tuple(path for path in map(_citation_path, tokens) if path)


def citation_unparsed(*, evidence: str) -> bool:
    """Return whether the evidence opened a quoted citation it never closed.

    Args:
        evidence: The verifier's evidence text.

    Returns:
        True when a ``"``, backtick or ``(`` opener has no closer.
    """
    return _tokens(evidence.replace("\\", "/"))[1]


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
        if evidence and citation_unparsed(evidence=evidence):
            # The record keeps the verifier's words, marked: the citation
            # could not be read, so it cannot count as evidence below.
            logger.warning(
                "Verification answer {} left a quoted citation unterminated.",
                position,
            )
            evidence = f"{CITATION_UNPARSED} {evidence}"
        elif outcome is not VerificationOutcome.CONFIRMED and not cited_paths(
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
