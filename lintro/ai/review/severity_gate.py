"""Mechanical severity gates for review findings (#1925, #2265).

Severity inflation is the norm for AI reviewers — in the corpus behind epic
#1905 one bot marked 92% of its findings P1. Under a verdict derived from open
severities (any P1 -> Blocked) an uncalibrated P1 makes every PR read blocked,
and a verdict nobody believes is worse than no verdict at all.

The gate is deliberately mechanical rather than a judgement call: a P1 must
carry a concrete ``failure_scenario``. One that does not is downgraded to P2 at
parse time and marked, so the downgrade is visible on the surfaces instead of
being an invisible edit of the model's output. Nothing is dropped, and no other
severity is touched.

The cross-chunk guard (#2265) is the second gate here and shares that posture.
A chunked review shows each chunk the other files at the base commit, so one
chunk can assert that a file changed elsewhere in the same pull request was
never touched. That assertion is checkable against the run's changed-file set
without asking the model anything, so a finding making it is tagged and moved
down one band rather than left to drive a false ``Blocked``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import replace

from loguru import logger

from lintro.ai.review.enums.cross_chunk_contradiction import (
    CrossChunkContradiction,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity

__all__ = [
    "CROSS_CHUNK_DOWNGRADE_REASON",
    "P1_DOWNGRADE_REASON",
    "UNCHANGED_CLAIM_PHRASES",
    "apply_cross_chunk_guard",
    "apply_p1_evidence_gate",
    "count_cross_chunk_contradictions",
    "count_downgrades",
    "cross_chunk_contradictions",
    "describe_cross_chunk_contradictions",
    "describe_downgrades",
    "downgraded_findings",
]

#: Reason shown wherever a gate-driven downgrade is surfaced. Kept as one
#: constant so the sticky (#1909), the per-review comment (#1910), and the log
#: line can never drift into describing the rule differently.
P1_DOWNGRADE_REASON = "no failure mechanism given"


def _needs_downgrade(*, finding: ReviewFinding) -> bool:
    """Return True when a finding is a P1 without a failure scenario.

    Args:
        finding: Finding to test.

    Returns:
        True when the P1 evidence gate applies to this finding. Questions are
        never gated: they carry no severity semantics to begin with.
    """
    if finding.is_question:
        return False
    return finding.severity is Severity.P1 and not finding.failure_scenario.strip()


def apply_p1_evidence_gate(
    *,
    findings: Sequence[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Downgrade P1 findings that report no concrete failure scenario.

    Args:
        findings: Findings as reported by the model, in payload order.

    Returns:
        The same findings in the same order, with ungated P1s rewritten to P2
        and marked via ``severity_downgraded``.
    """
    gated: list[ReviewFinding] = []
    for finding in findings:
        if not _needs_downgrade(finding=finding):
            gated.append(finding)
            continue
        logger.info(
            "Downgrading P1 finding {title!r} to P2: {reason}.",
            title=finding.title,
            reason=P1_DOWNGRADE_REASON,
        )
        gated.append(
            replace(
                finding,
                severity=Severity.P2,
                severity_downgraded=True,
            ),
        )
    return tuple(gated)


def downgraded_findings(
    *,
    findings: Iterable[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Select the findings the evidence gate downgraded.

    Args:
        findings: Findings to filter.

    Returns:
        The downgraded findings, in the order given.
    """
    return tuple(finding for finding in findings if finding.severity_downgraded)


def count_downgrades(*, findings: Iterable[ReviewFinding]) -> int:
    """Count the findings the evidence gate downgraded.

    Args:
        findings: Findings to count over.

    Returns:
        Number of downgraded findings.
    """
    return len(downgraded_findings(findings=findings))


def describe_downgrades(*, findings: Iterable[ReviewFinding]) -> str:
    """Build the one-line downgrade notice surfaces render.

    Args:
        findings: Findings to summarize.

    Returns:
        A line such as ``"1 finding downgraded to P2: no failure mechanism
        given"``, or an empty string when nothing was downgraded.
    """
    count = count_downgrades(findings=findings)
    if not count:
        return ""
    noun = "finding" if count == 1 else "findings"
    return f"{count} {noun} downgraded to {Severity.P2}: {P1_DOWNGRADE_REASON}"


#: Reason shown wherever a cross-chunk downgrade is surfaced (#2265). Kept as
#: one constant so the terminal, the review body, and the sticky can never
#: describe the guard differently.
CROSS_CHUNK_DOWNGRADE_REASON = "evidence claims a changed file was never touched"

#: Phrases that assert a file is at its base-revision state. The guard fires
#: only when one of these appears *and* the same text names a file the pull
#: request actually changed, so an ordinary cross-file reference is never
#: enough on its own. False negatives are the accepted cost of that pairing:
#: a missed contradiction leaves a finding at its reported severity, whereas a
#: false positive would quietly demote a real defect.
UNCHANGED_CLAIM_PHRASES: tuple[str, ...] = (
    "absent from the diff",
    "are unchanged",
    "are untouched",
    "at the base revision",
    "does not appear in the diff",
    "hasn't been updated",
    "haven't been updated",
    "is not in the diff",
    "is unchanged",
    "is untouched",
    "isn't in the diff",
    "left unchanged",
    "left untouched",
    "never updated",
    "not in the changed files",
    "not included in the diff",
    "not part of the diff",
    "not updated",
    "remain unchanged",
    "remain untouched",
    "remains unchanged",
    "remains untouched",
    "stays unchanged",
    "stays untouched",
    "was never touched",
    "was not changed",
    "was not modified",
    "was unchanged",
    "was untouched",
    "wasn't updated",
    "were unchanged",
    "were untouched",
)

#: Bands the guard walks down. P3 is terminal: it is already the lowest label,
#: so such a finding is tagged and left where it is rather than dropped.
_NEXT_BAND: dict[Severity, Severity] = {
    Severity.P1: Severity.P2,
    Severity.P2: Severity.P3,
    Severity.P3: Severity.P3,
}

#: Matches a file-ish token in prose: a path body followed by an extension.
_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]{1,12}")
#: Sentence boundary: a terminator followed by whitespace. Paths carry dots
#: with no following space, so ``a/b.py`` never splits.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+")


def _normalize_claim_text(*, text: str) -> str:
    """Fold a finding's prose into the form the phrase set is written in.

    Args:
        text: Raw prose from one or more finding fields.

    Returns:
        Lowercased text with typographic apostrophes folded and all runs of
        whitespace collapsed, so a phrase split across a line wrap still
        matches.
    """
    return " ".join(text.replace("’", "'").lower().split())


def _normalize_path_token(*, token: str) -> str:
    """Normalize a path for comparison against the changed-file set.

    Hyphens and underscores are folded together because the same file is
    routinely written both ways in prose (``migrate-docs-content.py`` for
    ``migrate_docs_content.py``), and a spelling difference is not evidence
    that two paths differ.

    Args:
        token: Path-like token as written in prose or in the changed set.

    Returns:
        The lowercased, forward-slashed, ``./``-stripped path with hyphens
        folded to underscores.
    """
    normalized = token.strip().replace("\\", "/").removeprefix("./").lower()
    return normalized.strip("/").replace("-", "_")


def _paths_match(*, token: str, path: str) -> bool:
    """Return True when a prose token names the given repository path.

    Args:
        token: Normalized path token taken from a finding's prose.
        path: Normalized repository-relative path.

    Returns:
        True on an exact match, when the token is a ``/``-delimited suffix of
        the changed path, or when the token carries exactly one extra leading
        segment (a repository name or a checkout prefix) in front of the
        changed path. A nested token never matches a shorter changed path
        (``src/utils.py`` does not match a changed root ``utils.py``), and a
        directory-qualified token never matches a different directory that
        shares a basename. Bare basenames are handled by the caller, which
        requires them to be unique among the changed paths.
    """
    if not token or not path:
        return False
    if token == path or path.endswith(f"/{token}"):
        return True
    # One extra leading segment (a repository name or checkout prefix) may be
    # dropped, but only when what remains is itself nested: a root-level
    # changed file is never reached by stripping a directory off the token.
    head, sep, rest = token.partition("/")
    return bool(sep) and "/" not in head and "/" in rest and rest == path


def _contradicted_paths(
    *,
    text: str,
    changed_paths: Iterable[str],
    own_file: str,
) -> tuple[str, ...]:
    """Select the changed paths a finding's prose names.

    Args:
        text: Normalized prose from the finding.
        changed_paths: Repository-relative paths the pull request changed.
        own_file: The finding's own file, excluded from the result.

    Returns:
        The changed paths named by the prose, other than the finding's own
        file, in changed-set order. A finding always talks about its own
        location, so counting that as a contradiction would fire the guard on
        ordinary prose.
    """
    tokens = {
        _normalize_path_token(token=match) for match in _PATH_TOKEN_RE.findall(text)
    }
    if not tokens:
        return ()
    own = _normalize_path_token(token=own_file)
    candidates = [
        (path, _normalize_path_token(token=path))
        for path in changed_paths
        if not (own and _paths_match(token=own, path=_normalize_path_token(token=path)))
    ]
    hits: list[str] = []
    for token in tokens:
        if "/" in token:
            matched = [
                path
                for path, norm in candidates
                if _paths_match(token=token, path=norm)
            ]
        else:
            # A bare basename is only evidence when it names exactly one
            # changed file; `utils.py` in a PR that touched two of them is a
            # guess, and a guess must not downgrade a finding.
            matched = [
                path for path, norm in candidates if norm.rsplit("/", 1)[-1] == token
            ]
            if len(matched) != 1:
                matched = []
        for path in matched:
            if path not in hits:
                hits.append(path)
    return tuple(hits)


def _finding_claim_text(*, finding: ReviewFinding) -> str:
    """Join the finding fields that carry its evidence.

    Args:
        finding: Finding to read.

    Returns:
        Normalized prose covering the title, description, root cause, and
        failure scenario. The fix text is excluded: it describes what *should*
        happen to a file, not what the diff contains.
    """
    # Fields are joined with a terminator so a claim in one field can never
    # share a sentence with a path named in another: the sentence rule is
    # only meaningful inside the prose the model actually wrote as one unit.
    return _normalize_claim_text(
        text=". ".join(
            (
                finding.title,
                finding.description,
                finding.cause,
                finding.failure_scenario,
            ),
        ),
    )


def _contradicted_changed_paths(
    *,
    finding: ReviewFinding,
    changed_paths: Sequence[str],
) -> tuple[str, ...]:
    """Return the changed paths a finding claims were never touched.

    Args:
        finding: Finding to test.
        changed_paths: Repository-relative paths the pull request changed.

    Returns:
        The contradicted paths, empty unless both halves hold: the prose
        carries an explicit unchanged claim *and* it names a changed file
        other than the finding's own.
    """
    if not changed_paths:
        return ()
    text = _finding_claim_text(finding=finding)
    # Both halves must sit in the *same sentence*: a finding that says one
    # unrelated file is unchanged and, elsewhere, names a changed file is not
    # contradicting the diff. Within a sentence, co-occurrence is enough — the
    # claim is not parsed for which noun it predicates. That is the accepted
    # trade-off: the cost of a false positive is a visible one-band downgrade
    # of a finding whose own sentence mixes an unchanged claim with a changed
    # file, never a dropped finding.
    hits: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not any(phrase in sentence for phrase in UNCHANGED_CLAIM_PHRASES):
            continue
        for path in _contradicted_paths(
            text=sentence,
            changed_paths=changed_paths,
            own_file=finding.file,
        ):
            if path not in hits:
                hits.append(path)
    return tuple(hits)


def apply_cross_chunk_guard(
    *,
    findings: Sequence[ReviewFinding],
    changed_paths: Sequence[str],
) -> tuple[ReviewFinding, ...]:
    """Downgrade findings whose evidence contradicts the changed-file set.

    Chunked reviews hand each chunk the other files at the base commit, so a
    chunk that sees only one side of a paired change can report in good faith
    that the other side "was never updated". The claim is mechanically
    checkable against the run's changed paths, which makes the contradiction a
    reliable marker of a chunk-local view regardless of which grouping rule
    produced it. Nothing is asked of the model and nothing is dropped: the
    finding keeps its prose, gains a
    :attr:`ReviewFinding.cross_chunk_contradiction` tag, and moves down one
    severity band so a phantom P1 can no longer block on its own.

    Args:
        findings: Findings for this run, in payload order.
        changed_paths: Every repository-relative path the pull request
            changed, not just the paths in one chunk.

    Returns:
        The same findings in the same order, with contradicted ones tagged and
        moved down one band.
    """
    guarded: list[ReviewFinding] = []
    for finding in findings:
        if finding.cross_chunk_contradiction is not None:
            # Already guarded: the tag records the one-band move, so a second
            # pass over the same finding must not move it again.
            guarded.append(finding)
            continue
        contradicted = (
            ()
            if finding.is_question
            else _contradicted_changed_paths(
                finding=finding,
                changed_paths=changed_paths,
            )
        )
        if not contradicted:
            guarded.append(finding)
            continue
        downgraded = _NEXT_BAND[finding.severity]
        logger.info(
            "Downgrading finding {title!r} to {severity}: {reason} ({paths}).",
            title=finding.title,
            severity=downgraded,
            reason=CROSS_CHUNK_DOWNGRADE_REASON,
            paths=", ".join(contradicted),
        )
        guarded.append(
            replace(
                finding,
                severity=downgraded,
                cross_chunk_contradiction=(
                    CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED
                    if downgraded is not finding.severity
                    else CrossChunkContradiction.UNCHANGED_FILE_CLAIM_TAGGED
                ),
            ),
        )
    return tuple(guarded)


def cross_chunk_contradictions(
    *,
    findings: Iterable[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Select the findings the cross-chunk guard tagged.

    Args:
        findings: Findings to filter.

    Returns:
        The tagged findings, in the order given.
    """
    return tuple(
        finding for finding in findings if finding.cross_chunk_contradiction is not None
    )


def count_cross_chunk_contradictions(*, findings: Iterable[ReviewFinding]) -> int:
    """Count the findings the cross-chunk guard tagged (downgraded or P3 kept).

    Args:
        findings: Findings to count over.

    Returns:
        Number of tagged findings.
    """
    return len(cross_chunk_contradictions(findings=findings))


def describe_cross_chunk_contradictions(*, findings: Iterable[ReviewFinding]) -> str:
    """Build the one-line cross-chunk notice every surface renders.

    Args:
        findings: Findings to summarize.

    Returns:
        A line such as ``"1 finding downgraded: evidence claims a changed file
        was never touched"``, or an empty string when nothing was tagged.
    """
    tagged = cross_chunk_contradictions(findings=findings)
    count = len(tagged)
    if not count:
        return ""
    lowered = sum(
        1
        for finding in tagged
        if finding.cross_chunk_contradiction
        is CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED
    )
    noun = "finding" if count == 1 else "findings"
    detail = f"{lowered} downgraded one band" if lowered else "none downgraded, P3 kept"
    return (
        f"{count} {noun} tagged as cross-chunk contradictions ({detail}): "
        f"{CROSS_CHUNK_DOWNGRADE_REASON}"
    )
