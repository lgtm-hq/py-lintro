"""Cross-chunk contradiction guard (issue #2265).

A chunked review hands each chunk the other files at the base commit, so a
chunk can assert in good faith that a file this pull request changed was never
touched. These tests pin that such a finding is tagged and moved down one
severity band, that an ordinary cross-file reference is not, that the count
reaches every surface, and that a run the guard never touched renders exactly
as it did before the guard existed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.cross_chunk_contradiction import CrossChunkContradiction
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import (
    match_findings,
    review_findings_from_unposted,
)
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.github_sticky import build_sticky_comment
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.orchestrator import run_review_async
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.severity_gate import (
    UNCHANGED_CLAIM_PHRASES,
    apply_cross_chunk_guard,
    count_cross_chunk_contradictions,
    cross_chunk_contradictions,
    describe_cross_chunk_contradictions,
)

_CHANGED = ("scripts/migrate_docs_content.py", "tests/unit/test_migrate_docs.py")

_METADATA = ReviewMetadata(
    model="claude-sonnet-4-6",
    provider="anthropic",
    context_window=200_000,
    depth=1,
    chunks_total=1,
    chunks_current=1,
    files_reviewed=2,
    files_total=2,
    checklist_items=0,
)


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a review finding for guard tests.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    fields: dict[str, Any] = {
        "severity": Severity.P1,
        "category": "correctness",
        "file": "scripts/migrate_docs_content.py",
        "line": 12,
        "title": "Rename breaks the caller",
        "description": "The renamed helper has no caller update.",
        "cause": "The symbol moved.",
        "fix": "Update the caller.",
        "confidence": "high",
    }
    fields.update(overrides)
    return ReviewFinding(**fields)


def _guard(**overrides: Any) -> ReviewFinding:
    """Run the guard over one finding and return the result.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The finding as the guard left it.
    """
    return apply_cross_chunk_guard(
        findings=(_finding(**overrides),),
        changed_paths=_CHANGED,
    )[0]


def _result_with(
    *,
    result: ReviewResult,
    findings: tuple[ReviewFinding, ...],
) -> ReviewResult:
    """Return ``result`` carrying the given findings.

    Args:
        result: Base review result.
        findings: Findings to stamp on the result.

    Returns:
        A copy of the result with the findings replaced.
    """
    return replace(result, findings=findings)


def _body(*, result: ReviewResult) -> str:
    """Render the per-review GitHub body through the public builder.

    Args:
        result: Review result to render.

    Returns:
        The rendered Markdown body.
    """
    prior_state = ReviewState()
    match = match_findings(
        previous=prior_state,
        findings=result.findings,
        round_number=prior_state.next_round,
        head_sha="fb740b2",
    )
    return build_review_body(
        result=result,
        prior_state=prior_state,
        match=match,
        head_sha="fb740b2",
        transport="cli",
        auth_mode="subscription",
    )


def _sticky(*, result: ReviewResult) -> str:
    """Render the sticky comment through the public builder.

    Args:
        result: Review result to render.

    Returns:
        The rendered sticky comment body.
    """
    return build_sticky_comment(
        result=result,
        transport="cli",
        auth_mode="subscription",
    )


def _terminal(*, result: ReviewResult) -> str:
    """Render the terminal review output to a string.

    Args:
        result: Review result to render.

    Returns:
        The captured terminal text.
    """
    console = Console(width=200, force_terminal=False, no_color=True)
    with console.capture() as capture:
        render_review_terminal(result=result, console=console)
    return capture.get()


# --- the rule -----------------------------------------------------------------


def test_claim_about_a_changed_file_is_tagged_and_downgraded() -> None:
    """A P1 asserting a changed file is untouched drops to P2 and is tagged."""
    finding = _guard(
        description=(
            "Changed files list is only migrate_docs_content.py; "
            "tests/unit/test_migrate_docs.py is untouched."
        ),
    )

    assert_that(finding.severity).is_equal_to(Severity.P2)
    assert_that(finding.cross_chunk_contradiction).is_equal_to(
        CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED,
    )


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        (Severity.P1, Severity.P2),
        (Severity.P2, Severity.P3),
        (Severity.P3, Severity.P3),
    ],
    ids=["band=P1", "band=P2", "band=P3"],
)
def test_the_guard_moves_a_finding_down_exactly_one_band(
    reported: Severity,
    expected: Severity,
) -> None:
    """Each band steps down once; P3 is the floor and is tagged in place.

    Args:
        reported: Severity the model reported.
        expected: Severity the guard should leave behind.
    """
    finding = _guard(
        severity=reported,
        description="tests/unit/test_migrate_docs.py was never updated.",
    )

    assert_that(finding.severity).is_equal_to(expected)
    assert_that(finding.cross_chunk_contradiction).is_not_none()


def test_the_guard_is_idempotent() -> None:
    """A second pass leaves a tagged finding exactly where the first left it.

    The CLI used to re-run the guard over findings finalize had already
    guarded when it replayed unposted findings, so a contradicted P1 became a
    P3 (#2268 review). The tag now short-circuits the guard.
    """
    once = _guard(
        severity=Severity.P1,
        description="tests/unit/test_migrate_docs.py was never updated.",
    )

    (twice,) = apply_cross_chunk_guard(findings=(once,), changed_paths=_CHANGED)

    assert_that(twice).is_equal_to(once)
    assert_that(twice.severity).is_equal_to(Severity.P2)


def test_nothing_is_dropped_by_the_guard() -> None:
    """A contradicted finding keeps its prose and its place in the payload."""
    findings = apply_cross_chunk_guard(
        findings=(
            _finding(),
            _finding(description="tests/unit/test_migrate_docs.py is unchanged."),
        ),
        changed_paths=_CHANGED,
    )

    assert_that(findings).is_length(2)
    assert_that(findings[1].description).contains("is unchanged")
    assert_that(findings[1].fix).is_equal_to("Update the caller.")


@pytest.mark.parametrize("phrase", UNCHANGED_CLAIM_PHRASES)
def test_every_claim_phrase_fires_the_guard(phrase: str) -> None:
    """Each phrase in the shared set is a live claim, not dead vocabulary.

    Sourced from the constant rather than restated, so a phrase added without
    a working matcher fails here instead of silently never firing.

    Args:
        phrase: One phrase from the shared claim set.
    """
    finding = _guard(
        description=f"The file tests/unit/test_migrate_docs.py {phrase} the helper.",
    )

    assert_that(finding.cross_chunk_contradiction).is_not_none()


def test_the_claim_phrase_set_stays_minimal_and_folded() -> None:
    """The set is matched against folded text and holds no dead entries.

    A phrase is dead when another phrase is a substring of it, because the
    shorter one always matches first; keeping the set sorted and free of such
    pairs is what makes it reviewable as one list.
    """
    phrases = list(UNCHANGED_CLAIM_PHRASES)

    assert_that(phrases).is_equal_to([phrase.lower() for phrase in phrases])
    assert_that(phrases).is_equal_to(sorted(set(phrases)))
    assert_that(
        [
            (outer, inner)
            for outer in phrases
            for inner in phrases
            if inner != outer and inner in outer
        ],
    ).is_empty()


def test_a_claim_about_an_unchanged_file_is_left_alone() -> None:
    """The claim is only a contradiction when the diff disagrees with it."""
    finding = _guard(
        description="docs/legacy/setup.md is untouched by this pull request.",
    )

    assert_that(finding.severity).is_equal_to(Severity.P1)
    assert_that(finding.cross_chunk_contradiction).is_none()


def test_an_ordinary_cross_file_reference_is_left_alone() -> None:
    """Naming a changed file without claiming it is unchanged never fires."""
    finding = _guard(
        description=(
            "The caller in tests/unit/test_migrate_docs.py passes the old "
            "argument order."
        ),
    )

    assert_that(finding.severity).is_equal_to(Severity.P1)
    assert_that(finding.cross_chunk_contradiction).is_none()


def test_a_claim_about_the_findings_own_file_is_left_alone() -> None:
    """A finding always names its own file, so that hit proves nothing."""
    finding = _guard(
        description="scripts/migrate_docs_content.py is untouched below line 40.",
    )

    assert_that(finding.cross_chunk_contradiction).is_none()


def test_an_empty_changed_set_never_fires_the_guard() -> None:
    """With no changed paths there is nothing for a claim to contradict."""
    findings = apply_cross_chunk_guard(
        findings=(
            _finding(description="tests/unit/test_migrate_docs.py is untouched"),
        ),
        changed_paths=(),
    )

    assert_that(findings[0].cross_chunk_contradiction).is_none()


def test_a_question_is_never_downgraded() -> None:
    """Questions carry no severity semantics, so the guard skips them."""
    finding = _guard(
        kind=FindingKind.QUESTION,
        description="tests/unit/test_migrate_docs.py is untouched — intended?",
    )

    assert_that(finding.severity).is_equal_to(Severity.P1)
    assert_that(finding.cross_chunk_contradiction).is_none()


def test_a_downgraded_phantom_p1_no_longer_blocks() -> None:
    """The point of the guard: a chunk-local claim cannot render a PR blocked."""
    contradicted = _finding(
        description="tests/unit/test_migrate_docs.py is untouched.",
    )
    blocked = ReviewResult(
        metadata=_METADATA,
        summary="",
        findings=(contradicted,),
    )
    guarded = replace(
        blocked,
        findings=apply_cross_chunk_guard(
            findings=blocked.findings,
            changed_paths=_CHANGED,
        ),
    )

    assert_that(blocked.readiness_verdict).is_equal_to(ReviewVerdict.BLOCKED)
    assert_that(guarded.readiness_verdict).is_equal_to(
        ReviewVerdict.CHANGES_REQUESTED,
    )


# --- path matching ------------------------------------------------------------


@pytest.mark.parametrize(
    "claimed",
    [
        "tests/unit/test_migrate_docs.py",
        "tests/unit/test-migrate-docs.py",
        "test_migrate_docs.py",
        "Tests/Unit/Test_Migrate_Docs.py",
        "./tests/unit/test_migrate_docs.py",
        "repo/tests/unit/test_migrate_docs.py",
        r"tests\unit\test_migrate_docs.py",
    ],
    ids=[
        "spelling=exact",
        "spelling=hyphenated",
        "spelling=basename",
        "spelling=mixed_case",
        "spelling=dot_slash",
        "spelling=extra_prefix",
        "spelling=backslashes",
    ],
)
def test_path_matching_tolerates_prose_spellings(claimed: str) -> None:
    """Prose names a file loosely; a spelling difference is not a new file.

    Args:
        claimed: How the finding's prose spells the changed test file.
    """
    finding = _guard(description=f"{claimed} is untouched.")

    assert_that(finding.cross_chunk_contradiction).is_not_none()


def test_a_similarly_named_unchanged_file_is_not_a_hit() -> None:
    """Tolerance stops at the basename; an unrelated file still misses."""
    finding = _guard(description="tests/unit/test_other_docs.py is untouched.")

    assert_that(finding.cross_chunk_contradiction).is_none()


def test_a_claim_split_across_lines_still_matches() -> None:
    """Wrapped prose is folded before matching, so line breaks do not hide it."""
    finding = _guard(
        description="tests/unit/test_migrate_docs.py was\nnever   updated.",
    )

    assert_that(finding.cross_chunk_contradiction).is_not_none()


def test_the_claim_may_come_from_the_failure_scenario() -> None:
    """Every evidence field is read, not just the description."""
    finding = _guard(
        description="The rename lands without its caller.",
        failure_scenario="tests/unit/test_migrate_docs.py is untouched at head.",
    )

    assert_that(finding.cross_chunk_contradiction).is_not_none()


def test_the_fix_text_is_not_read_as_evidence() -> None:
    """A fix says what *should* change; it is not a claim about the diff."""
    finding = _guard(
        fix="Leave tests/unit/test_migrate_docs.py untouched and revert here.",
    )

    assert_that(finding.cross_chunk_contradiction).is_none()


# --- helpers ------------------------------------------------------------------


def test_selection_and_count_report_only_tagged_findings() -> None:
    """The selectors mirror ``downgraded_findings`` for the new tag."""
    findings = apply_cross_chunk_guard(
        findings=(
            _finding(),
            _finding(description="tests/unit/test_migrate_docs.py is untouched."),
        ),
        changed_paths=_CHANGED,
    )

    assert_that(cross_chunk_contradictions(findings=findings)).is_length(1)
    assert_that(count_cross_chunk_contradictions(findings=findings)).is_equal_to(1)


def test_the_notice_is_empty_when_the_guard_did_not_fire() -> None:
    """A clean run produces no sentence at all."""
    assert_that(
        describe_cross_chunk_contradictions(findings=(_finding(),)),
    ).is_empty()


@pytest.mark.parametrize(
    ("tagged", "expected"),
    [
        (
            1,
            "1 finding tagged as cross-chunk contradictions (1 downgraded one band): evidence claims a changed file was never touched",
        ),
        (
            2,
            "2 findings tagged as cross-chunk contradictions (2 downgraded one band): evidence claims a changed file was never touched",
        ),
    ],
    ids=["count=one", "count=many"],
)
def test_the_notice_states_the_count_and_the_reason(
    tagged: int,
    expected: str,
) -> None:
    """The one-line notice is the copy every surface shares.

    Args:
        tagged: How many findings the guard tagged.
        expected: The exact sentence the surfaces render.
    """
    findings = apply_cross_chunk_guard(
        findings=tuple(
            _finding(description="tests/unit/test_migrate_docs.py is untouched.")
            for _ in range(tagged)
        ),
        changed_paths=_CHANGED,
    )

    assert_that(describe_cross_chunk_contradictions(findings=findings)).is_equal_to(
        expected,
    )


# --- surfaces -----------------------------------------------------------------


@pytest.fixture
def guarded_result(sample_review_result: ReviewResult) -> ReviewResult:
    """Return a review result whose first finding the guard downgraded.

    Args:
        sample_review_result: Shared review result fixture.

    Returns:
        The result carrying one tagged finding.
    """
    contradicted = replace(
        sample_review_result.findings[0],
        description="tests/unit/test_migrate_docs.py is untouched.",
    )
    findings = apply_cross_chunk_guard(
        findings=(contradicted, *sample_review_result.findings[1:]),
        changed_paths=_CHANGED,
    )
    return _result_with(result=sample_review_result, findings=findings)


def test_terminal_output_states_the_downgrade(
    guarded_result: ReviewResult,
    sample_review_result: ReviewResult,
) -> None:
    """The terminal never edits a severity without saying so.

    Args:
        guarded_result: Result carrying a tagged finding.
        sample_review_result: Shared review result fixture.
    """
    text = _terminal(result=guarded_result)

    assert_that(text).contains("1 finding tagged")
    assert_that(_terminal(result=sample_review_result)).does_not_contain(
        "tagged as cross-chunk contradictions",
    )


def test_review_body_states_the_downgrade(
    guarded_result: ReviewResult,
    sample_review_result: ReviewResult,
) -> None:
    """The posted review body carries the note in its run-stats block.

    Args:
        guarded_result: Result carrying a tagged finding.
        sample_review_result: Shared review result fixture.
    """
    body = _body(result=guarded_result)

    assert_that(body).contains("1 finding tagged")
    assert_that(body).contains("chunk-local")
    assert_that(_body(result=sample_review_result)).does_not_contain("chunk-local")


def test_sticky_comment_states_the_downgrade(
    guarded_result: ReviewResult,
    sample_review_result: ReviewResult,
) -> None:
    """The sticky shares its wording with the review body.

    Args:
        guarded_result: Result carrying a tagged finding.
        sample_review_result: Shared review result fixture.
    """
    sticky = _sticky(result=guarded_result)

    assert_that(sticky).contains("1 finding tagged")
    assert_that(sticky).contains("chunk-local")
    assert_that(_sticky(result=sample_review_result)).does_not_contain("chunk-local")


def test_the_two_github_surfaces_render_the_same_note(
    guarded_result: ReviewResult,
) -> None:
    """One helper feeds both surfaces, so their wording cannot drift.

    Args:
        guarded_result: Result carrying a tagged finding.
    """
    from lintro.ai.review.github_render import format_cross_chunk_note

    note = format_cross_chunk_note(findings=guarded_result.findings)

    assert_that(_body(result=guarded_result)).contains(note)
    assert_that(_sticky(result=guarded_result)).contains(note)


def test_json_payload_counts_the_downgrades(
    guarded_result: ReviewResult,
    sample_review_result: ReviewResult,
) -> None:
    """The payload reports the count and the per-finding tag.

    Args:
        guarded_result: Result carrying a tagged finding.
        sample_review_result: Shared review result fixture.
    """
    payload = review_result_to_dict(result=guarded_result)

    assert_that(payload["cross_chunk_contradictions"]).is_equal_to(1)
    assert_that(payload["findings"][0]["cross_chunk_contradiction"]).is_equal_to(
        str(CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED),
    )
    assert_that(json.loads(json.dumps(payload))["cross_chunk_contradictions"])
    clean = review_result_to_dict(result=sample_review_result)
    assert_that(clean["cross_chunk_contradictions"]).is_equal_to(0)
    assert_that(clean["findings"][0]["cross_chunk_contradiction"]).is_none()


def test_an_unaffected_run_renders_identically_on_every_surface(
    sample_review_result: ReviewResult,
) -> None:
    """A run the guard never touched changes no rendered byte.

    Args:
        sample_review_result: Shared review result fixture.
    """
    baseline = sample_review_result
    explicit = _result_with(
        result=baseline,
        findings=tuple(
            replace(finding, cross_chunk_contradiction=None)
            for finding in baseline.findings
        ),
    )

    assert_that(_terminal(result=explicit)).is_equal_to(_terminal(result=baseline))
    assert_that(_body(result=explicit)).is_equal_to(_body(result=baseline))
    assert_that(_sticky(result=explicit)).is_equal_to(_sticky(result=baseline))


# --- orchestrator wiring ------------------------------------------------------


_DIFF = """\
diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1,2 @@
 value = 1
+value = 2
diff --git a/tests/test_a.py b/tests/test_a.py
index 3333333..4444444 100644
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1 +1,2 @@
 assert value == 1
+assert value == 2
"""


def _contradicting_response() -> AIResponse:
    """Return a chunk response whose P1 contradicts the changed-file set.

    Returns:
        A parseable provider response carrying one contradicted P1.
    """
    payload = {
        "summary": {"headline": "Adds a constant.", "walkthrough": []},
        "checklist": [],
        "findings": [
            {
                "severity": "P1",
                "category": "correctness",
                "file": "src/a.py",
                "line": 2,
                "title": "Test never updated for the new value",
                "description": "tests/test_a.py is untouched in this round.",
                "cause": "The chunk only carries the source file.",
                "fix": "Update the test.",
                "confidence": "high",
                "failure_scenario": "CI passes on a stale assertion.",
            },
        ],
        "verdict_reasoning": {
            "deciding_factor": "Stale test.",
            "failure_mechanism": "Assertion never runs.",
            "files_needing_attention": [],
        },
        "file_assessments": [],
    }
    return AIResponse(
        content=json.dumps(payload),
        model="claude-sonnet-4-6",
        provider=AIProvider.ANTHROPIC,
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.0,
    )


async def test_a_full_run_downgrades_a_contradicted_p1(tmp_path: Path) -> None:
    """The guard is wired at finalize with the run's full changed set.

    Locks the orchestrator wiring: without it a chunk-local claim would still
    reach the verdict as a P1 and render the pull request blocked.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/a.py",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="tests/test_a.py",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=_DIFF,
        pr_metadata=None,
        repo_root=str(tmp_path),
    )
    provider = MagicMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    provider.capabilities.supports_sessions = False

    with patch(
        "lintro.ai.review.orchestrator.call_ai",
        new=AsyncMock(return_value=_contradicting_response()),
    ):
        result = await run_review_async(
            context=context,
            provider=provider,
            ai_config=AIConfig(
                enabled=True,
                review=True,
                transport=AITransport.API,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="",
            classifications=[],
        )

    tagged = cross_chunk_contradictions(findings=result.findings)

    assert_that(tagged).is_not_empty()
    assert_that(tagged[0].severity).is_equal_to(Severity.P2)


def test_claim_and_changed_path_in_different_sentences_do_not_fire() -> None:
    """An unchanged claim about one file plus a changed file named elsewhere is not a contradiction."""
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    finding = _finding(
        file="src/app.py",
        description=(
            "The legacy config loader in src/legacy.py is untouched by this "
            "change. The new helper in src/helpers.py is called from here."
        ),
    )

    guarded = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "src/helpers.py"),
    )

    assert_that(guarded[0].cross_chunk_contradiction).is_none()
    assert_that(guarded[0].severity).is_equal_to(finding.severity)


def test_claim_and_changed_path_in_the_same_sentence_fire() -> None:
    """The same claim about a changed file, in one sentence, is downgraded."""
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    finding = _finding(
        file="src/app.py",
        description="The helper in src/helpers.py is untouched by this change.",
    )

    guarded = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "src/helpers.py"),
    )

    assert_that(guarded[0].cross_chunk_contradiction).is_not_none()


def test_bare_basename_only_matches_a_unique_changed_file() -> None:
    """``utils.py`` in a PR that changed two of them is a guess, not evidence."""
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    finding = _finding(
        file="src/app.py",
        description="The helper in utils.py is untouched by this change.",
    )

    ambiguous = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "src/a/utils.py", "src/b/utils.py"),
    )
    unique = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "src/a/utils.py"),
    )

    assert_that(ambiguous[0].cross_chunk_contradiction).is_none()
    assert_that(unique[0].cross_chunk_contradiction).is_not_none()


def test_directory_qualified_token_does_not_match_a_different_directory() -> None:
    """``lib/utils.py`` never matches a changed ``src/utils.py``."""
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    finding = _finding(
        file="src/app.py",
        description="The helper in lib/utils.py is untouched by this change.",
    )

    guarded = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "src/utils.py"),
    )

    assert_that(guarded[0].cross_chunk_contradiction).is_none()


def test_notice_wording_separates_tagged_from_downgraded() -> None:
    """A tagged P3 is reported as kept, never as downgraded."""
    from lintro.ai.review.severity_gate import (
        apply_cross_chunk_guard,
        describe_cross_chunk_contradictions,
    )

    p3 = _finding(
        file="src/app.py",
        severity=Severity.P3,
        description="The helper in src/helpers.py is untouched by this change.",
    )
    p1 = replace(p3, severity=Severity.P1, title="blocker")

    only_p3 = apply_cross_chunk_guard(
        findings=(p3,),
        changed_paths=("src/app.py", "src/helpers.py"),
    )
    both = apply_cross_chunk_guard(
        findings=(p3, p1),
        changed_paths=("src/app.py", "src/helpers.py"),
    )

    assert_that(describe_cross_chunk_contradictions(findings=only_p3)).contains(
        "1 finding tagged as cross-chunk contradictions (none downgraded, P3 kept)",
    )
    assert_that(describe_cross_chunk_contradictions(findings=both)).contains(
        "2 findings tagged as cross-chunk contradictions (1 downgraded one band)",
    )
    assert_that(only_p3[0].cross_chunk_contradiction).is_equal_to(
        CrossChunkContradiction.UNCHANGED_FILE_CLAIM_TAGGED,
    )


def test_a_p2_moved_to_p3_counts_as_downgraded() -> None:
    """A P2 that became P3 is a downgrade, not a kept P3."""
    from lintro.ai.review.severity_gate import (
        apply_cross_chunk_guard,
        describe_cross_chunk_contradictions,
    )

    p2 = _finding(
        file="src/app.py",
        severity=Severity.P2,
        description="The helper in src/helpers.py is untouched by this change.",
    )

    guarded = apply_cross_chunk_guard(
        findings=(p2,),
        changed_paths=("src/app.py", "src/helpers.py"),
    )

    assert_that(guarded[0].severity).is_equal_to(Severity.P3)
    assert_that(guarded[0].cross_chunk_contradiction).is_equal_to(
        CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED,
    )
    assert_that(describe_cross_chunk_contradictions(findings=guarded)).contains(
        "(1 downgraded one band)",
    )


def test_claim_and_path_in_different_fields_do_not_fire() -> None:
    """A changed path in the title and a claim in the description are separate sentences."""
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    finding = _finding(
        file="src/app.py",
        title="src/helpers.py call site",
        description="The legacy loader in src/legacy.py is untouched by this change",
        cause="",
        failure_scenario="",
    )

    guarded = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "src/helpers.py"),
    )

    assert_that(guarded[0].cross_chunk_contradiction).is_none()


def test_guard_changed_paths_includes_rename_and_copy_sources() -> None:
    """The guard's changed set carries previous paths; agent scoping does not."""
    from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
    from lintro.ai.review.models.changed_file import ChangedFile
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.orchestrator import guard_changed_paths

    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/helpers.py",
                status=ChangedFileStatus.RENAMED,
                previous_path="src/legacy_helpers.py",
                additions=1,
                deletions=0,
            ),
            ChangedFile(path="src/app.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff="",
        pr_metadata=None,
        repo_root=".",
    )

    assert_that(guard_changed_paths(context=context)).is_equal_to(
        ("src/helpers.py", "src/legacy_helpers.py", "src/app.py"),
    )


def test_a_claim_about_a_rename_source_is_a_contradiction() -> None:
    """A claim that a rename's old path was never touched contradicts the diff."""
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    finding = _finding(
        file="src/app.py",
        description="The old module src/legacy_helpers.py is untouched by this change.",
    )

    guarded = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "src/helpers.py", "src/legacy_helpers.py"),
    )

    assert_that(guarded[0].cross_chunk_contradiction).is_not_none()


def test_nested_token_does_not_match_a_shorter_changed_path() -> None:
    """``src/utils.py`` in prose never matches a changed root ``utils.py``."""
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    finding = _finding(
        file="src/app.py",
        description="The helper in src/utils.py is untouched by this change.",
    )

    guarded = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "utils.py"),
    )

    assert_that(guarded[0].cross_chunk_contradiction).is_none()


def test_github_note_omits_the_band_clause_for_a_tagged_p3(
    sample_review_result: ReviewResult,
) -> None:
    """When only a P3 was tagged, the posted note does not claim a downgrade.

    Args:
        sample_review_result: Shared review result fixture.
    """
    from lintro.ai.review.github_render import format_cross_chunk_note
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    p3 = _finding(
        file="src/app.py",
        severity=Severity.P3,
        description="The helper in src/helpers.py is untouched by this change.",
    )
    guarded = apply_cross_chunk_guard(
        findings=(p3,),
        changed_paths=("src/app.py", "src/helpers.py"),
    )
    del sample_review_result

    note = format_cross_chunk_note(findings=guarded)

    assert_that(note).contains("none downgraded, P3 kept")
    assert_that(note).does_not_contain("one band lower")


def test_still_uses_phrasing_alone_does_not_fire() -> None:
    """Ordinary incomplete-update prose is not an unchanged-file claim."""
    from lintro.ai.review.severity_gate import apply_cross_chunk_guard

    finding = _finding(
        file="src/app.py",
        description="src/app.py still uses the helper exported by src/helpers.py.",
    )

    guarded = apply_cross_chunk_guard(
        findings=(finding,),
        changed_paths=("src/app.py", "src/helpers.py"),
    )

    assert_that(guarded[0].cross_chunk_contradiction).is_none()


# --- persistence across rounds ------------------------------------------------


def test_the_tag_is_persisted_on_the_record_only_when_set() -> None:
    """A tagged record round-trips its tag; an untagged one serializes as before."""
    tagged = _guard(description="tests/unit/test_migrate_docs.py was never updated.")
    (record,) = match_findings(
        previous=None,
        findings=(tagged,),
        round_number=1,
    ).records

    payload = record.to_dict()
    restored = FindingRecord.from_dict(payload)

    assert_that(payload["cross_chunk_contradiction"]).is_equal_to(
        CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED.value,
    )
    assert_that(restored).is_not_none()
    assert restored is not None
    assert_that(restored.cross_chunk_contradiction).is_equal_to(
        CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED,
    )
    plain = match_findings(
        previous=None,
        findings=(_finding(description="An ordinary finding."),),
        round_number=1,
    ).records[0]
    assert_that(plain.to_dict()).does_not_contain_key("cross_chunk_contradiction")
    assert_that(
        FindingRecord.from_dict(
            {**plain.to_dict(), "cross_chunk_contradiction": "bogus"},
        ),
    ).is_not_none()


def test_a_replayed_finding_keeps_its_tag_and_its_band() -> None:
    """Replay restores the tag, so the notice counts it and no guard re-fires.

    A downgraded P2 replayed without its tag looked like an untagged P2 with
    the same claim: the notice omitted it and a second guard pass could have
    moved it to P3 (#2268 review).
    """
    tagged = _guard(
        severity=Severity.P1,
        description="tests/unit/test_migrate_docs.py was never updated.",
    )
    prior = ReviewState(
        findings=match_findings(
            previous=None,
            findings=(tagged,),
            round_number=1,
        ).records,
    )

    (replayed,) = review_findings_from_unposted(
        prior=prior,
        current=(),
        reviewed_paths=frozenset(),
    )
    (guarded_again,) = apply_cross_chunk_guard(
        findings=(replayed,),
        changed_paths=_CHANGED,
    )

    assert_that(replayed.severity).is_equal_to(Severity.P2)
    assert_that(replayed.cross_chunk_contradiction).is_equal_to(
        CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED,
    )
    assert_that(guarded_again).is_equal_to(replayed)
    assert_that(count_cross_chunk_contradictions(findings=(replayed,))).is_equal_to(1)
