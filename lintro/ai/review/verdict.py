"""Derivation and presentation of the merge-readiness verdict.

The readiness verdict is **derived in code**, never asked of the model: a
model-scored verdict drifts from the findings it is supposed to summarize, and
a reader who sees "Ready" above a P1 finding stops trusting the whole comment.
The rubric below is the single source of truth — surfaces render it as
fine-print under the reasoning rather than restating it, and the review prompt
tells the model the verdict is computed so it writes only the reasoning.
"""

from __future__ import annotations

from collections.abc import Iterable

from lintro.ai.review.enums.readiness_verdict import ReadinessVerdict
from lintro.ai.review.models.review_finding import ReviewFinding, Severity

__all__ = [
    "VERDICT_LABELS",
    "VERDICT_RUBRIC_FINE_PRINT",
    "derive_readiness_verdict",
    "resolve_bullet_finding",
    "verdict_label",
]

#: Human-readable labels for each verdict, used by every surface.
VERDICT_LABELS: dict[ReadinessVerdict, str] = {
    ReadinessVerdict.BLOCKED: "Blocked",
    ReadinessVerdict.CHANGES_REQUESTED: "Changes requested",
    ReadinessVerdict.NITS_ONLY: "Nits only",
    ReadinessVerdict.READY: "Ready",
}

#: Fine-print rendered under the verdict reasoning. Mirrors
#: :func:`derive_readiness_verdict` exactly; change both together.
VERDICT_RUBRIC_FINE_PRINT: str = (
    "Verdict is derived from open findings: any P1 -> Blocked; "
    "else any P2 -> Changes requested; else any P3 -> Nits only; "
    "else Ready."
)


def derive_readiness_verdict(
    *,
    findings: Iterable[ReviewFinding],
) -> ReadinessVerdict:
    """Derive the merge-readiness verdict from open finding severities.

    Args:
        findings: The run's open findings. Findings already resolved in an
            earlier round must be filtered out by the caller.

    Returns:
        ``BLOCKED`` when any P1 is open, ``CHANGES_REQUESTED`` when any P2 is
        open, ``NITS_ONLY`` when only P3s are open, and ``READY`` when nothing
        is open.
    """
    severities = {finding.severity for finding in findings}
    if Severity.P1 in severities:
        return ReadinessVerdict.BLOCKED
    if Severity.P2 in severities:
        return ReadinessVerdict.CHANGES_REQUESTED
    if Severity.P3 in severities:
        return ReadinessVerdict.NITS_ONLY
    return ReadinessVerdict.READY


def verdict_label(*, verdict: ReadinessVerdict) -> str:
    """Return the display label for a readiness verdict.

    Args:
        verdict: Verdict to label.

    Returns:
        The human-readable label, e.g. ``"Changes requested"``.
    """
    return VERDICT_LABELS[verdict]


def resolve_bullet_finding(
    *,
    finding_ref: str,
    findings: Iterable[ReviewFinding],
) -> ReviewFinding | None:
    """Resolve a summary bullet's finding reference to a finding.

    References are ``file:line`` strings written by the model. A reference
    whose line does not match falls back to the first finding in the same
    file, so a bullet about a real finding still gets severity-marked when the
    model is a line or two off. Cross-run fingerprints (#1906) will replace the
    reference format later; callers only depend on this function.

    Args:
        finding_ref: The bullet's raw reference, possibly empty.
        findings: Candidate findings to resolve against.

    Returns:
        The matching finding, or ``None`` when the reference is empty or does
        not name any reviewed file.
    """
    reference = finding_ref.strip()
    if not reference:
        return None

    path, _, line_text = reference.rpartition(":")
    if not path:
        path, line = reference, None
    else:
        try:
            line = int(line_text)
        except ValueError:
            path, line = reference, None

    candidates = [finding for finding in findings if finding.file == path]
    if not candidates:
        return None
    if line is None:
        return candidates[0]
    return next(
        (finding for finding in candidates if finding.line == line),
        candidates[0],
    )
