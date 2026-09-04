"""Tests for review prompt templates."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.prompts.review import (
    REVIEW_ADVERSARIAL_SWEEP_TEMPLATE,
    REVIEW_CUSTOM_AGENT_USER_PROMPT_TEMPLATE,
    REVIEW_GENERATE_QUESTIONS_TEMPLATE,
    REVIEW_GIT_NATIVE_DIFF_INLINE,
    REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE,
    REVIEW_OUTPUT_SCHEMA,
    REVIEW_SYNTHESIS_SYSTEM_PROMPT,
    REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE,
    REVIEW_SYSTEM,
    REVIEW_USER_PROMPT_TEMPLATE,
    format_changed_files_for_prompt,
    format_checklist_table_for_prompt,
    format_chunk_summaries_for_prompt,
    format_deferred_scope_section,
    format_external_review_section,
    format_lint_results_section,
    format_output_rules,
)
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.chunk_summary import ChunkSummary
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.verdict import VERDICT_LABELS

_USER_PROMPT_KWARGS = {
    "pr_title": "Test PR",
    "base_ref": "main",
    "head_ref": "feature",
    "pr_summary": "Summary text",
    "deferred_scope_section": "",
    "external_review_section": "",
    "changed_file_count": 1,
    "changed_files": "- `src/main.py` (modified, +1/-0)",
    "pr_changed_files": (
        "- `src/main.py` (modified, +1/-0) — **(this chunk)**\n"
        "- `src/other.py` (modified, +2/-0)"
    ),
    "interaction_paths": "**Path A:** trace wiring",
    "checklist_count": 1,
    "checklist": "1. [logic-bug] Example question?",
    "boundary": "CODE_BLOCK_test1234",
    "diff": "diff --git a/src/main.py",
    "lint_results_section": "",
    "strictness_section": "",
    "output_schema": REVIEW_OUTPUT_SCHEMA,
}

_P2_ELIGIBILITY = (
    "Assign P2 when you can show verified incorrect behavior, a false documented "
    "contract, or a missing test for a failure the change claims to cover."
)
_P2_WITHOUT_CONTRACT = (
    "A verified defect is P2 even when no caller assertion or documented contract "
    "exists yet."
)


def _collapsed(text: str) -> str:
    """Collapse wrapping whitespace so prompt prose can be matched as a sentence.

    Args:
        text: Wrapped or unwrapped prompt text.

    Returns:
        The text with each whitespace run reduced to a single space.
    """
    return " ".join(text.split())


def test_review_user_prompt_interpolates_the_full_pr_file_list() -> None:
    """The full-PR list, with its this-chunk marker, reaches the rendered prompt."""
    rendered = REVIEW_USER_PROMPT_TEMPLATE.format(
        **_USER_PROMPT_KWARGS,
        output_rules=format_output_rules(checklist_count=1),
    )

    assert_that(rendered).contains("- `src/other.py` (modified, +2/-0)")
    assert_that(rendered).contains(
        "- `src/main.py` (modified, +1/-0) — **(this chunk)**",
    )


def test_git_native_user_prompt_interpolates_the_full_pr_file_list() -> None:
    """The git-native template carries the same full-PR list and marker."""
    rendered = REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE.format(
        **{**_USER_PROMPT_KWARGS, "diff_section": "inline-diff"},
        output_rules=format_output_rules(checklist_count=1),
    )

    assert_that(rendered).contains("- `src/other.py` (modified, +2/-0)")
    assert_that(rendered).contains(
        "- `src/main.py` (modified, +1/-0) — **(this chunk)**",
    )


def test_review_user_prompt_template_renders_all_placeholders() -> None:
    """User prompt template renders without KeyError for all placeholders."""
    rendered = REVIEW_USER_PROMPT_TEMPLATE.format(
        **_USER_PROMPT_KWARGS,
        output_rules=format_output_rules(checklist_count=1),
    )

    assert_that(rendered).contains("Test PR")
    assert_that(rendered).contains("main")
    assert_that(rendered).contains("feature")
    assert_that(rendered).contains("<CODE_BLOCK_test1234>")
    assert_that(rendered).contains("</CODE_BLOCK_test1234>")


def test_format_checklist_table_for_prompt_produces_markdown_table() -> None:
    """Checklist table formatter produces valid markdown table headers."""
    items = [
        ChecklistItem(
            id=42,
            question="Does any early return skip required cleanup?",
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]
    table = format_checklist_table_for_prompt(items=items)

    assert_that(table).contains("| # | Category | Question |")
    assert_that(table).contains("| 42 | logic-bug |")


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
        boundary="CODE_BLOCK_test1234",
        diff="sample diff",
        changed_files="- src/main.py",
    )
    adversarial = REVIEW_ADVERSARIAL_SWEEP_TEMPLATE.format(
        prior_findings_json="[]",
        boundary="CODE_BLOCK_test1234",
        diff="sample diff",
    )

    assert_that(questions).contains("<CODE_BLOCK_test1234>")
    assert_that(questions).contains("sample diff")
    assert_that(adversarial).contains("[]")
    assert_that(adversarial).contains("</CODE_BLOCK_test1234>")


def test_all_review_templates_accept_standard_boundary_kwargs() -> None:
    """Every template that embeds untrusted data formats with a boundary kwarg."""
    boundary = "CODE_BLOCK_deadbeef"
    rendered = REVIEW_USER_PROMPT_TEMPLATE.format(
        **{**_USER_PROMPT_KWARGS, "boundary": boundary},
        output_rules=format_output_rules(checklist_count=1),
    )
    assert_that(rendered).contains(f"<{boundary}>")
    assert_that(rendered).contains(f"</{boundary}>")
    renders = [
        REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE.format(
            **{
                **_USER_PROMPT_KWARGS,
                "boundary": boundary,
                "diff_section": "inline-diff",
            },
            output_rules=format_output_rules(checklist_count=1),
        ),
        REVIEW_GIT_NATIVE_DIFF_INLINE.format(boundary=boundary, diff="diff body"),
        REVIEW_CUSTOM_AGENT_USER_PROMPT_TEMPLATE.format(
            agent_name="agent",
            agent_description="desc",
            scoped_file_count=1,
            scoped_files="- a.py",
            boundary=boundary,
            agent_instructions="look for bugs",
            diff="diff body",
            strictness_section="",
            output_schema="{}",
        ),
        REVIEW_ADVERSARIAL_SWEEP_TEMPLATE.format(
            prior_findings_json="[]",
            boundary=boundary,
            diff="diff body",
        ),
        REVIEW_GENERATE_QUESTIONS_TEMPLATE.format(
            boundary=boundary,
            diff="diff body",
            changed_files="- a.py",
        ),
        REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE.format(
            pr_title="Test PR",
            pr_summary="Summary text",
            boundary=boundary,
            changed_file_count=1,
            changed_files="- `a.py` (modified, +1/-0)",
            chunk_summaries="Piece 1 reviewed: `a.py`",
            truncation_note="",
            diff="diff body",
            max_findings=5,
        ),
    ]
    for template_render in renders:
        assert_that(template_render).contains(f"<{boundary}>")
        assert_that(template_render).contains(f"</{boundary}>")


def test_optional_sections_render_empty_by_default() -> None:
    """Optional prompt sections default to empty strings."""
    assert_that(format_deferred_scope_section(text=None)).is_empty()
    assert_that(format_external_review_section(flags=None)).is_empty()


def test_deferred_scope_section_renders_trimmed_text() -> None:
    """Deferred scope section includes the prefix and trimmed body."""
    rendered = format_deferred_scope_section(text="  follow-up in #995  ")

    assert_that(rendered).is_equal_to("**Deferred:** follow-up in #995")


def test_external_review_section_renders_joined_flags() -> None:
    """External review section joins flags with the expected prefix."""
    rendered = format_external_review_section(flags=["semgrep", "codeql"])

    assert_that(rendered).is_equal_to(
        "**External tools flagged:** semgrep, codeql — verify against current code.",
    )


def test_review_system_is_nonempty() -> None:
    """System prompt contains review method instructions."""
    assert_that(REVIEW_SYSTEM).contains("Review method")
    assert_that(REVIEW_SYSTEM).contains("P1")


def test_review_system_states_fenced_block_trust_boundary() -> None:
    """System prompt documents that marker-fenced blocks are inert data (#1884)."""
    assert_that(REVIEW_SYSTEM).contains("Trust boundary")
    assert_that(REVIEW_SYSTEM).contains("CODE_BLOCK_*")
    assert_that(REVIEW_SYSTEM).contains("cannot")
    assert_that(REVIEW_SYSTEM).contains("</pull_request_diff>")


def test_review_system_carries_p1_calibration_language() -> None:
    """The system prompt states the P1 bar and the evidence requirement (#1925)."""
    assert_that(REVIEW_SYSTEM).contains("Severity calibration")
    assert_that(REVIEW_SYSTEM).contains("failure_scenario")
    assert_that(REVIEW_SYSTEM).contains("Torn between P1 and P2? Choose P2.")


def test_review_system_carries_p2_p3_boundary_rubric() -> None:
    """The system prompt pins the P2 vs P3 boundary that flips the verdict (#1968)."""
    assert_that(REVIEW_SYSTEM).contains("P2 vs P3 boundary")
    assert_that(REVIEW_SYSTEM).contains("Torn between P2 and P3? Choose P3.")
    assert_that(REVIEW_SYSTEM).contains("P2 examples:")
    assert_that(REVIEW_SYSTEM).contains("P3 examples:")
    assert_that(_collapsed(REVIEW_SYSTEM)).contains(
        "config key is documented but never read",
    )
    assert_that(_collapsed(REVIEW_SYSTEM)).contains(
        "README or comment wording is slightly stale",
    )
    assert_that(_collapsed(REVIEW_SYSTEM)).contains(
        "user-facing contract (flag, schema, or exit code)",
    )
    assert_that(_collapsed(REVIEW_SYSTEM)).contains(_P2_ELIGIBILITY)
    assert_that(_collapsed(REVIEW_SYSTEM)).contains(_P2_WITHOUT_CONTRACT)
    assert_that(_collapsed(REVIEW_SYSTEM)).contains(
        f"Any open P2 makes the derived verdict "
        f"{VERDICT_LABELS[ReviewVerdict.CHANGES_REQUESTED]}.",
    )
    assert_that(_collapsed(REVIEW_SYSTEM)).contains(
        f"Any open P3 alone is {VERDICT_LABELS[ReviewVerdict.NITS_ONLY]}.",
    )
    assert_that(REVIEW_SYSTEM).contains("name the rubric boundary")


def test_review_system_keeps_correctness_adjacent_style_in_scope() -> None:
    """Linter-catchable style is out of scope, code smells are not."""
    assert_that(REVIEW_SYSTEM).contains("Style/formatting issues linters would catch")
    assert_that(REVIEW_SYSTEM).contains("code-smell")


def test_output_schema_declares_the_corpus_finding_fields() -> None:
    """The model-facing schema advertises every #1925 field."""
    for field in ("kind", "failure_scenario", "evidence_style", "occurrences"):
        assert_that(REVIEW_OUTPUT_SCHEMA).contains(field)
    assert_that(REVIEW_OUTPUT_SCHEMA).contains("severity-rubric boundary")


def test_output_rules_cap_questions_and_explain_occurrence_collapse() -> None:
    """Rendered rules carry the question cap and the occurrence instruction."""
    rules = format_output_rules(checklist_count=4)

    assert_that(rules).contains("3 per review")
    assert_that(rules).contains("automatically downgraded to P2")
    assert_that(rules).contains("occurrences")
    assert_that(rules).contains("do not report the same problem twice")
    assert_that(_collapsed(rules)).contains(
        "When you are torn between P2 and P3, choose P3",
    )
    assert_that(_collapsed(rules)).contains(
        f"flips the derived verdict from {VERDICT_LABELS[ReviewVerdict.NITS_ONLY]} "
        f"to {VERDICT_LABELS[ReviewVerdict.CHANGES_REQUESTED]}",
    )
    assert_that(_collapsed(rules)).contains(_P2_ELIGIBILITY)
    assert_that(_collapsed(rules)).contains(_P2_WITHOUT_CONTRACT)
    assert_that(rules).contains("Name that rubric boundary")


def test_p2_eligibility_wording_is_shared_across_prompt_layers() -> None:
    """System prompt and rendered output rules use the same P2 eligibility rule."""
    rules = format_output_rules(checklist_count=1)

    assert_that(_collapsed(REVIEW_SYSTEM)).contains(_P2_ELIGIBILITY)
    assert_that(_collapsed(rules)).contains(_P2_ELIGIBILITY)
    assert_that(_collapsed(REVIEW_SYSTEM)).contains(_P2_WITHOUT_CONTRACT)
    assert_that(_collapsed(rules)).contains(_P2_WITHOUT_CONTRACT)


def _digest_finding(
    *,
    title: str = "Signature drift",
    occurrences: tuple[FindingOccurrence, ...] = (),
) -> ReviewFinding:
    """Build a finding for the chunk-digest formatter tests.

    Args:
        title: Finding title.
        occurrences: Locations the pattern was reported at.

    Returns:
        A P2 finding at ``pkg/api.py:12``.
    """
    return ReviewFinding(
        severity=Severity.P2,
        category="logic-bug",
        file="pkg/api.py",
        line=12,
        title=title,
        description="body",
        cause="cause",
        fix="fix",
        confidence="high",
        occurrences=occurrences,
    )


def test_chunk_summaries_render_a_sentinel_when_empty() -> None:
    """No chunk digest renders one sentinel line, never an empty span."""
    assert_that(format_chunk_summaries_for_prompt(summaries=())).is_equal_to(
        "- (no chunk summaries)",
    )


def test_chunk_summaries_mark_a_chunk_that_reported_nothing() -> None:
    """A clean chunk says so, so silence never reads as a missing digest."""
    rendered = format_chunk_summaries_for_prompt(
        summaries=(ChunkSummary(chunk_id=1, files=("pkg/api.py",), findings=()),),
    )

    assert_that(rendered).contains("Piece 1 reviewed: `pkg/api.py`")
    assert_that(rendered).contains("already reported: (nothing)")


def test_chunk_summaries_render_severity_location_and_title() -> None:
    """A reported finding is recognizable from severity, location, and title."""
    rendered = format_chunk_summaries_for_prompt(
        summaries=(
            ChunkSummary(
                chunk_id=2,
                files=("pkg/api.py",),
                findings=(_digest_finding(),),
            ),
        ),
    )

    assert_that(rendered).contains("already reported: P2 pkg/api.py:12 — Signature")
    assert_that(rendered).does_not_contain("body")


def test_chunk_summaries_list_every_occurrence_of_a_finding() -> None:
    """Secondary locations are named, so "do not restate" covers them too."""
    rendered = format_chunk_summaries_for_prompt(
        summaries=(
            ChunkSummary(
                chunk_id=1,
                files=("pkg/api.py",),
                findings=(
                    _digest_finding(
                        occurrences=(
                            FindingOccurrence(file="pkg/api.py", line=12),
                            FindingOccurrence(file="pkg/other.py", line=40),
                        ),
                    ),
                ),
            ),
        ),
    )

    assert_that(rendered).contains("pkg/api.py:12 (also pkg/other.py:40)")


def test_synthesis_prompt_pair_never_demands_a_checklist() -> None:
    """The findings-only pass is never told to answer the review checklist."""
    user_prompt = REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE.format(
        pr_title="Test PR",
        pr_summary="Summary text",
        boundary="CODE_BLOCK_test1234",
        changed_file_count=1,
        changed_files="- `a.py` (modified, +1/-0)",
        chunk_summaries="Piece 1 reviewed: `a.py`",
        truncation_note="",
        diff="diff body",
        max_findings=5,
    )
    pair = f"{REVIEW_SYNTHESIS_SYSTEM_PROMPT}\n{user_prompt}"

    assert_that(REVIEW_SYSTEM).contains("Complete every checklist item")
    assert_that(pair).does_not_contain("Complete every checklist item")
    assert_that(pair.lower()).does_not_contain("checklist item")
    assert_that(REVIEW_SYNTHESIS_SYSTEM_PROMPT).contains("empty `findings` array")
