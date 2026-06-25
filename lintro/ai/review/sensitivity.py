"""Review sensitivity policy resolution, prompts, and finding filters."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.config.review_config import ReviewSensitivityOverrides

__all__ = [
    "ReviewSensitivityPolicy",
    "filter_findings_by_policy",
    "format_strictness_prompt_section",
    "resolve_sensitivity_policy",
]

_MIGRATION_NOTE_PATTERN = re.compile(
    r"migration|undocumented|lockstep|rename note|parity|old→new|old->new",
    re.IGNORECASE,
)
_DOC_PATH_PREFIXES = ("docs/", "doc/")


@dataclass(frozen=True, slots=True)
class ReviewSensitivityPolicy:
    """Resolved sensitivity rules for one review run."""

    strictness: ReviewStrictness
    report_migration_notes: bool
    report_doc_drift: bool
    report_test_gaps: bool


def resolve_sensitivity_policy(
    *,
    strictness: ReviewStrictness,
    overrides: ReviewSensitivityOverrides | None = None,
) -> ReviewSensitivityPolicy:
    """Resolve effective sensitivity from preset plus optional overrides.

    Args:
        strictness: Selected strictness preset.
        overrides: Optional per-category overrides from configuration.

    Returns:
        Resolved policy for prompts and post-filtering.
    """
    preset = _PRESETS[strictness]
    override = overrides or ReviewSensitivityOverrides()
    return ReviewSensitivityPolicy(
        strictness=strictness,
        report_migration_notes=_resolve_override(
            override.migration_notes,
            preset.report_migration_notes,
        ),
        report_doc_drift=_resolve_override(
            override.doc_drift,
            preset.report_doc_drift,
        ),
        report_test_gaps=_resolve_override(
            override.test_gaps,
            preset.report_test_gaps,
        ),
    )


def format_strictness_prompt_section(*, policy: ReviewSensitivityPolicy) -> str:
    """Build prompt instructions for the active sensitivity policy.

    Args:
        policy: Resolved sensitivity policy.

    Returns:
        Markdown section injected into the review user prompt.
    """
    if policy.strictness == ReviewStrictness.FOCUSED:
        suppressed: list[str] = []
        if not policy.report_migration_notes:
            suppressed.append(
                "missing migration notes or old→new command mapping callouts",
            )
        if not policy.report_doc_drift:
            suppressed.append(
                "documentation-only contract drift (stale framework labels, "
                "wording mismatches, undocumented CI/local parity in docs)",
            )
        if not policy.report_test_gaps:
            suppressed.append("P3 test-coverage nits")

        suppressed_text = "; ".join(suppressed) if suppressed else "doc-only P3 nits"
        return (
            "### Sensitivity (focused)\n\n"
            "Complete every checklist item honestly. Prioritize merge blockers "
            "and behavioral/CI integration issues.\n\n"
            f"Do **not** add findings for: {suppressed_text}.\n"
            "Still record those checklist **yes** answers in `checklist`, but "
            "omit them from `findings` unless they are P1/P2 or affect runtime, "
            "CI, or caller behavior outside documentation."
        )

    if policy.strictness == ReviewStrictness.THOROUGH:
        hunts: list[str] = [
            "logic bugs and CI integration gaps",
        ]
        if policy.report_migration_notes:
            hunts.append(
                "missing migration notes when scripts, defaults, or public "
                "entry points change",
            )
        if policy.report_doc_drift:
            hunts.append(
                "documentation lockstep gaps (README vs architecture vs testing "
                "docs, stale tool names, undocumented CI/local command parity)",
            )
        if policy.report_test_gaps:
            hunts.append("test gaps including untested justfile/recipe wiring")
        hunt_text = "; ".join(hunts)
        return (
            "### Sensitivity (thorough)\n\n"
            f"Actively hunt for: {hunt_text}.\n"
            "Report checklist **yes** answers as findings, including P3 "
            "documentation and migration nits when they would confuse "
            "contributors or break runbooks."
        )

    return (
        "### Sensitivity (balanced)\n\n"
        "Report every checklist **yes** as a finding. Prioritize cross-file "
        "integration bugs over isolated nits, but do not drop legitimate P3 "
        "issues when checklist items fire."
    )


def filter_findings_by_policy(
    *,
    findings: tuple[ReviewFinding, ...],
    policy: ReviewSensitivityPolicy,
) -> tuple[ReviewFinding, ...]:
    """Post-filter findings according to the resolved sensitivity policy.

    Args:
        findings: Raw findings from the model.
        policy: Resolved sensitivity policy.

    Returns:
        Findings that should be shown to the user.
    """
    if policy.strictness == ReviewStrictness.BALANCED and _uses_default_overrides(
        policy,
    ):
        return findings

    kept = [
        finding
        for finding in findings
        if _should_report_finding(finding=finding, policy=policy)
    ]
    return tuple(kept)


def _uses_default_overrides(policy: ReviewSensitivityPolicy) -> bool:
    preset = _PRESETS[policy.strictness]
    return (
        policy.report_migration_notes == preset.report_migration_notes
        and policy.report_doc_drift == preset.report_doc_drift
        and policy.report_test_gaps == preset.report_test_gaps
    )


def _should_report_finding(
    *,
    finding: ReviewFinding,
    policy: ReviewSensitivityPolicy,
) -> bool:
    if finding.severity in {"P1", "P2"}:
        return True

    category = finding.category
    if category == ReviewCategory.CONTRACT_DRIFT.value and not policy.report_doc_drift:
        return False

    if category == ReviewCategory.TEST_GAP.value and not policy.report_test_gaps:
        return False

    if category == ReviewCategory.BREAKING_CHANGE.value:
        if not policy.report_migration_notes and _is_migration_doc_finding(finding):
            return True if not _is_doc_path(finding.file) else False
        if (
            not policy.report_doc_drift
            and _is_doc_path(finding.file)
            and _is_migration_doc_finding(finding)
        ):
            return False

    if (
        category == ReviewCategory.INTEGRATION.value
        and not policy.report_doc_drift
        and _is_doc_path(finding.file)
        and _is_migration_doc_finding(finding)
    ):
        return False

    return True


def _is_doc_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    if normalized.endswith(".md"):
        return True
    return normalized.startswith(_DOC_PATH_PREFIXES)


def _is_migration_doc_finding(finding: ReviewFinding) -> bool:
    haystack = f"{finding.title} {finding.description}"
    if _MIGRATION_NOTE_PATTERN.search(haystack):
        return True
    if _is_doc_path(finding.file) and finding.severity == "P3":
        return True
    return False


def _resolve_override(value: bool | None, preset: bool) -> bool:
    return preset if value is None else value


_PRESETS: dict[ReviewStrictness, ReviewSensitivityPolicy] = {
    ReviewStrictness.FOCUSED: ReviewSensitivityPolicy(
        strictness=ReviewStrictness.FOCUSED,
        report_migration_notes=False,
        report_doc_drift=False,
        report_test_gaps=True,
    ),
    ReviewStrictness.BALANCED: ReviewSensitivityPolicy(
        strictness=ReviewStrictness.BALANCED,
        report_migration_notes=True,
        report_doc_drift=True,
        report_test_gaps=True,
    ),
    ReviewStrictness.THOROUGH: ReviewSensitivityPolicy(
        strictness=ReviewStrictness.THOROUGH,
        report_migration_notes=True,
        report_doc_drift=True,
        report_test_gaps=True,
    ),
}
