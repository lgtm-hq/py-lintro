"""Tests for review prompt templates."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.prompts.review import (
    REVIEW_ADVERSARIAL_SWEEP_TEMPLATE,
    REVIEW_GENERATE_QUESTIONS_TEMPLATE,
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_SYSTEM,
    REVIEW_USER_PROMPT_TEMPLATE,
    format_changed_files_for_prompt,
    format_checklist_table_for_prompt,
    format_deferred_scope_section,
    format_external_review_section,
    format_lint_results_section,
)
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem


def test_review_user_prompt_template_renders_all_placeholders() -> None:
    """User prompt template renders without KeyError for all placeholders."""
    rendered = REVIEW_USER_PROMPT_TEMPLATE.format(
        pr_title="Test PR",
        base_ref="main",
        head_ref="feature",
        pr_summary="Summary text",
        deferred_scope_section="",
        external_review_section="",
        changed_file_count=1,
        changed_files="- `src/main.py` (modified, +1/-0)",
        interaction_paths="**Path A:** trace wiring",
        checklist_count=1,
        checklist="1. [logic-bug] Example question?",
        diff="diff --git a/src/main.py",
        lint_results_section="",
        output_schema=REVIEW_OUTPUT_SCHEMA,
    )

    assert_that(rendered).contains("Test PR")
    assert_that(rendered).contains("main")
    assert_that(rendered).contains("feature")


def test_format_checklist_table_for_prompt_produces_markdown_table() -> None:
    """Checklist table formatter produces valid markdown table headers."""
    items = [
        ChecklistItem(
            id=1,
            question="Does any early return skip required cleanup?",
            triggers=[],
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]
    table = format_checklist_table_for_prompt(items=items)

    assert_that(table).contains("| # | Category | Question |")
    assert_that(table).contains("| 1 | logic-bug |")


def test_format_changed_files_for_prompt_lists_files_with_status() -> None:
    """Changed files formatter includes path and status."""
    files = [
        ChangedFile(
            path="src/main.py",
            status="modified",
            additions=3,
            deletions=1,
        ),
    ]
    rendered = format_changed_files_for_prompt(files=files)

    assert_that(rendered).contains("src/main.py")
    assert_that(rendered).contains("modified")


def test_format_lint_results_section_empty_when_no_digest() -> None:
    """Empty lint digest renders as empty string."""
    assert_that(format_lint_results_section(digest=None)).is_empty()
    assert_that(format_lint_results_section(digest="")).is_empty()


def test_format_lint_results_section_wraps_digest() -> None:
    """Non-empty lint digest is wrapped in lint_results tags."""
    rendered = format_lint_results_section(digest="ruff: 2 issues")

    assert_that(rendered).starts_with("<lint_results>")
    assert_that(rendered).contains("ruff: 2 issues")


def test_depth_templates_render_without_key_error() -> None:
    """Depth 2 and 3 templates render with required placeholders."""
    questions = REVIEW_GENERATE_QUESTIONS_TEMPLATE.format(
        diff="sample diff",
        changed_files="- src/main.py",
    )
    adversarial = REVIEW_ADVERSARIAL_SWEEP_TEMPLATE.format(
        prior_findings_json="[]",
        diff="sample diff",
    )

    assert_that(questions).contains("sample diff")
    assert_that(adversarial).contains("[]")


def test_optional_sections_render_empty_by_default() -> None:
    """Optional prompt sections default to empty strings."""
    assert_that(format_deferred_scope_section(text=None)).is_empty()
    assert_that(format_external_review_section(flags=None)).is_empty()


def test_review_system_is_nonempty() -> None:
    """System prompt contains review method instructions."""
    assert_that(REVIEW_SYSTEM).contains("Review method")
    assert_that(REVIEW_SYSTEM).contains("P1")
