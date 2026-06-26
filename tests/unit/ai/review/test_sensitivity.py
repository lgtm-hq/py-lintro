"""Tests for review sensitivity policy and filtering."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.sensitivity import (
    filter_findings_by_policy,
    format_strictness_prompt_section,
    resolve_sensitivity_policy,
)
from lintro.config.review_config import ReviewSensitivityOverrides


def _finding(
    *,
    severity: str = "P3",
    category: str = ReviewCategory.BREAKING_CHANGE.value,
    file: str = "docs/architecture.md",
    title: str = "No CI script migration note",
) -> ReviewFinding:
    return ReviewFinding(
        severity=severity,
        category=category,
        file=file,
        line=10,
        title=title,
        description="Missing migration guidance.",
        cause="Docs not updated.",
        fix="Add migration note.",
        confidence="high",
        checklist_ids=(5,),
    )


def test_resolve_sensitivity_policy_focused_disables_doc_nits() -> None:
    """Focused preset suppresses migration notes and doc drift."""
    policy = resolve_sensitivity_policy(strictness=ReviewStrictness.FOCUSED)

    assert_that(policy.report_migration_notes).is_false()
    assert_that(policy.report_doc_drift).is_false()
    assert_that(policy.report_test_gaps).is_true()


def test_resolve_sensitivity_policy_thorough_keeps_doc_hunting() -> None:
    """Thorough preset enables doc/migration hunting without changing chunking."""
    policy = resolve_sensitivity_policy(strictness=ReviewStrictness.THOROUGH)

    assert_that(policy.report_migration_notes).is_true()
    assert_that(policy.report_doc_drift).is_true()


def test_resolve_sensitivity_policy_overrides_migration_notes() -> None:
    """Config overrides can re-enable migration notes on focused preset."""
    policy = resolve_sensitivity_policy(
        strictness=ReviewStrictness.FOCUSED,
        overrides=ReviewSensitivityOverrides(migration_notes=True),
    )

    assert_that(policy.report_migration_notes).is_true()


def test_filter_findings_focused_drops_doc_migration_note() -> None:
    """Focused mode filters doc-only migration note findings."""
    policy = resolve_sensitivity_policy(strictness=ReviewStrictness.FOCUSED)
    findings = (_finding(),)

    filtered = filter_findings_by_policy(findings=findings, policy=policy)

    assert_that(filtered).is_empty()


def test_filter_findings_focused_keeps_behavioral_breaking_change() -> None:
    """Focused mode keeps non-doc breaking changes."""
    policy = resolve_sensitivity_policy(strictness=ReviewStrictness.FOCUSED)
    findings = (
        _finding(
            file="package.json",
            title="Default test script semantics changed",
        ),
    )

    filtered = filter_findings_by_policy(findings=findings, policy=policy)

    assert_that(filtered).is_length(1)


def test_filter_findings_focused_keeps_p2_integration() -> None:
    """Focused mode never filters P2 integration findings."""
    policy = resolve_sensitivity_policy(strictness=ReviewStrictness.FOCUSED)
    findings = (
        _finding(
            severity="P2",
            category=ReviewCategory.INTEGRATION.value,
            file=".github/workflows/test-core.yml",
            title="CI runs auth-setup twice",
        ),
    )

    filtered = filter_findings_by_policy(findings=findings, policy=policy)

    assert_that(filtered).is_length(1)


def test_format_strictness_prompt_section_includes_focused_rules() -> None:
    """Focused prompt section tells the model to skip doc-only nits."""
    policy = resolve_sensitivity_policy(strictness=ReviewStrictness.FOCUSED)
    section = format_strictness_prompt_section(policy=policy)

    assert_that(section).contains("focused")
    assert_that(section).contains("Do **not** add findings")
