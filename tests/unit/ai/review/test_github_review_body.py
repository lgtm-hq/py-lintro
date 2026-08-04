"""Tests for the per-review GitHub comment body (issue #1910)."""

from __future__ import annotations

from dataclasses import replace

from assertpy import assert_that

from lintro import __version__ as lintro_version
from lintro.ai.review.enums.file_skip_reason import FileSkipReason
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.github_review_body import (
    REVIEW_BODY_FOOTER,
    build_review_body,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.skipped_file import SkippedFile


def _body(
    *,
    result: ReviewResult,
    prior_state: ReviewState,
    head_sha: str = "fb740b2aaaa",
    sticky_url: str = "",
    transport: str = "",
    auth_mode: str = "",
    config_source: str = "",
    new_commits: int | None = None,
) -> str:
    """Build a review body for ``result`` against ``prior_state``.

    Args:
        result: Review result under test.
        prior_state: State decoded before this round.
        head_sha: Head sha reviewed in this round.
        sticky_url: URL of the sticky status comment.
        transport: Provider transport used for the round.
        auth_mode: Authentication mode used by the transport.
        config_source: Description of where the run's settings came from.
        new_commits: Commits pushed since the previous round.

    Returns:
        The rendered Markdown body.
    """
    match = match_findings(
        previous=prior_state,
        findings=result.findings,
        round_number=prior_state.next_round,
        head_sha=head_sha,
    )
    return build_review_body(
        result=result,
        prior_state=prior_state,
        match=match,
        head_sha=head_sha,
        sticky_url=sticky_url,
        transport=transport,
        auth_mode=auth_mode,
        config_source=config_source,
        new_commits=new_commits,
    )


def _prior_state(*, rounds: int, sha: str = "484f51caaa") -> ReviewState:
    """Build a prior state carrying ``rounds`` completed runs.

    Args:
        rounds: Number of completed rounds to record.
        sha: Head sha of the most recent completed round.

    Returns:
        A review state whose next round is ``rounds + 1``.
    """
    return ReviewState(
        runs=tuple(
            RunRecord(round=index, sha=sha if index == rounds else f"old{index}")
            for index in range(1, rounds + 1)
        ),
    )


# --- header ----------------------------------------------------------------


def test_header_counts_findings_and_omits_delta_on_first_round(
    sample_review_result: ReviewResult,
) -> None:
    """Round 1 has no previous round, so no resolved delta is claimed."""
    body = _body(result=sample_review_result, prior_state=ReviewState())

    assert_that(body).contains("🔎 **Lintro review — 2 findings posted**")
    assert_that(body).contains("round 1")
    assert_that(body).does_not_contain("resolved since round")


def test_header_states_resolved_delta_against_previous_round(
    sample_review_result: ReviewResult,
) -> None:
    """A round that clears a prior finding reports it in the header."""
    first = match_findings(
        previous=ReviewState(),
        findings=sample_review_result.findings,
        round_number=1,
        head_sha="484f51caaa",
    )
    prior_state = ReviewState(
        runs=(RunRecord(round=1, sha="484f51caaa"),),
        findings=first.records,
    )
    trimmed = replace(
        sample_review_result,
        findings=sample_review_result.findings[:1],
    )

    body = _body(result=trimmed, prior_state=prior_state)

    assert_that(body).contains("🔎 **Lintro review — 1 finding posted**")
    assert_that(body).contains("✔ 1 resolved since round 1")
    assert_that(body).contains("round 2")


def test_header_reports_zero_resolved_rather_than_hiding_the_delta(
    sample_review_result: ReviewResult,
) -> None:
    """A later round with nothing fixed says so instead of dropping the delta."""
    first = match_findings(
        previous=ReviewState(),
        findings=sample_review_result.findings,
        round_number=1,
        head_sha="484f51caaa",
    )
    prior_state = ReviewState(
        runs=(RunRecord(round=1, sha="484f51caaa"),),
        findings=first.records,
    )

    body = _body(result=sample_review_result, prior_state=prior_state)

    assert_that(body).contains("✔ 0 resolved since round 1")


def test_header_names_the_reviewed_commit_range(
    sample_review_result: ReviewResult,
) -> None:
    """The header pins the round to a short base..head commit range."""
    body = _body(
        result=sample_review_result,
        prior_state=_prior_state(rounds=2),
    )

    assert_that(body).contains("commits `484f51c..fb740b2`")


# --- fix prompt and the dedup rule -----------------------------------------


def test_prompt_panel_points_at_sticky_when_scopes_are_identical(
    sample_review_result: ReviewResult,
) -> None:
    """Round 1 findings are all open findings, so the panel is not duplicated."""
    body = _body(
        result=sample_review_result,
        prior_state=ReviewState(),
        sticky_url="https://github.com/owner/name/pull/7#issuecomment-1",
    )

    assert_that(body).contains("⚡ Fix prompt: identical to the")
    assert_that(body).contains("#issuecomment-1")
    assert_that(body).does_not_contain("Fix prompt — this round's")
    assert_that(body).does_not_contain("Show prompt")


def test_pointer_renders_unlinked_when_the_sticky_id_is_unknown(
    sample_review_result: ReviewResult,
) -> None:
    """A sticky created in this same run has no id yet; no dead link is emitted."""
    body = _body(result=sample_review_result, prior_state=ReviewState())

    assert_that(body).contains("sticky comment's fix-all this round")
    assert_that(body).does_not_contain("]()")


def test_prompt_panel_renders_when_older_findings_remain_open(
    sample_review_result: ReviewResult,
) -> None:
    """With carried-over findings the two scopes differ, so both panels exist."""
    first = match_findings(
        previous=ReviewState(),
        findings=sample_review_result.findings,
        round_number=1,
        head_sha="484f51caaa",
    )
    prior_state = ReviewState(
        runs=(RunRecord(round=1, sha="484f51caaa"),),
        findings=first.records,
    )
    # This round re-reports only the first finding; round 1's second finding
    # is still open (its file was not part of this round's diff), so the
    # sticky's all-open scope is strictly larger than this round's scope.
    reported = replace(
        sample_review_result,
        findings=sample_review_result.findings[:1],
    )
    match = match_findings(
        previous=prior_state,
        findings=reported.findings,
        round_number=2,
        head_sha="fb740b2aaa",
    )
    still_open = replace(match, records=(*match.records, *first.records[1:]))

    body = build_review_body(
        result=reported,
        prior_state=prior_state,
        match=still_open,
        head_sha="fb740b2aaa",
    )

    assert_that(body).contains("⚡ **Fix prompt — this round's 1 finding only**")
    assert_that(body).contains("<details><summary>Show prompt</summary>")
    assert_that(body).does_not_contain("identical to the")


def test_no_prompt_section_when_the_round_found_nothing(
    sample_review_result: ReviewResult,
) -> None:
    """A clean round has nothing to fix, so neither panel nor pointer appears."""
    clean = replace(sample_review_result, findings=())

    body = _body(result=clean, prior_state=ReviewState())

    assert_that(body).contains("🔎 **Lintro review — 0 findings posted**")
    assert_that(body).does_not_contain("Fix prompt")


# --- run stats -------------------------------------------------------------


def test_run_stats_are_visible_and_ordered(
    sample_review_result: ReviewResult,
) -> None:
    """Stats render outside a collapsible, model first, mechanics second."""
    body = _body(
        result=sample_review_result,
        prior_state=ReviewState(),
        transport="cli",
        auth_mode="subscription",
    )

    assert_that(body).contains("**📊 Run stats**")
    stats_index = body.index("**📊 Run stats**")
    details_index = body.index("<details>")
    assert_that(stats_index).is_less_than(details_index)
    assert_that(body).contains("| model | est. cost | tokens in | tokens out |")
    assert_that(body).contains("cli · subscription")
    assert_that(body).contains(f"| {lintro_version} |")


def test_estimated_token_counts_are_marked_approximate(
    sample_review_result: ReviewResult,
) -> None:
    """CLI-transport runs must not present estimates as reported figures."""
    estimated = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            token_usage_estimated=True,
        ),
    )

    body = _body(result=estimated, prior_state=ReviewState())

    assert_that(body).contains("~1,000")
    assert_that(body).contains("~$")


def test_config_source_note_renders_when_supplied(
    sample_review_result: ReviewResult,
) -> None:
    """The config provenance note explains why stats differ from the config."""
    body = _body(
        result=sample_review_result,
        prior_state=ReviewState(),
        config_source="`.lintro-config.yaml` + CLI overrides (--timeout 600)",
    )

    assert_that(body).contains(
        "Config source: `.lintro-config.yaml` + CLI overrides (--timeout 600)",
    )


def test_config_source_note_is_omitted_when_unknown(
    sample_review_result: ReviewResult,
) -> None:
    """No config source is better than an empty label."""
    body = _body(result=sample_review_result, prior_state=ReviewState())

    assert_that(body).does_not_contain("Config source:")


# --- commits ---------------------------------------------------------------


def test_commits_section_names_the_new_commit_count(
    sample_review_result: ReviewResult,
) -> None:
    """Later rounds state how much new work they looked at, and against what."""
    body = _body(
        result=sample_review_result,
        prior_state=_prior_state(rounds=2),
        new_commits=2,
    )

    assert_that(body).contains("<details><summary>📥 Commits</summary>")
    assert_that(body).contains(
        "This round reviewed the 2 new commits since round 2, " "`484f51c` → `fb740b2`",
    )
    assert_that(body).contains("full diff against `main`")


def test_commits_section_omits_an_unknown_count(
    sample_review_result: ReviewResult,
) -> None:
    """An unavailable commit listing must not become a fabricated number."""
    body = _body(result=sample_review_result, prior_state=_prior_state(rounds=2))

    assert_that(body).contains("This round reviewed the new commits since round 2")


def test_commits_section_describes_the_full_diff_on_round_one(
    sample_review_result: ReviewResult,
) -> None:
    """Round 1 has no previous head, so there is no delta to describe."""
    body = _body(result=sample_review_result, prior_state=ReviewState())

    assert_that(body).contains("This round reviewed the PR's full diff against `main`")


# --- files reviewed and skip reasons ---------------------------------------


def test_files_section_lists_skipped_files_with_reasons(
    sample_review_result: ReviewResult,
) -> None:
    """A skipped file states why, so a coverage gap cannot read as a clean pass."""
    metadata = replace(
        sample_review_result.metadata,
        reviewed_paths=("src/main.py", "tests/test_main.py"),
        skipped_files=(
            SkippedFile(path="docs/README.md", reason=FileSkipReason.PATH_FILTER),
            SkippedFile(
                path="pkg/item9.py",
                reason=FileSkipReason.REPETITIVE_DIFF,
                detail="same diff hunks as `pkg/item1.py`",
            ),
        ),
    )
    body = _body(
        result=replace(sample_review_result, metadata=metadata),
        prior_state=ReviewState(),
    )

    assert_that(body).contains("📁 Files reviewed (2) · 2 skipped")
    assert_that(body).contains("- `src/main.py`")
    assert_that(body).contains(
        "- `docs/README.md` — skipped (outside the requested --path filter)",
    )
    assert_that(body).contains("same diff hunks as `pkg/item1.py`")


def test_files_section_reports_no_skips_when_everything_was_reviewed(
    sample_review_result: ReviewResult,
) -> None:
    """With nothing skipped the summary carries no misleading skip count."""
    metadata = replace(
        sample_review_result.metadata,
        reviewed_paths=("src/main.py",),
    )
    body = _body(
        result=replace(sample_review_result, metadata=metadata),
        prior_state=ReviewState(),
    )

    assert_that(body).contains("📁 Files reviewed (1)")
    assert_that(body).does_not_contain("skipped")


def test_files_section_is_omitted_when_no_paths_were_recorded(
    sample_review_result: ReviewResult,
) -> None:
    """An empty file list is dropped rather than rendered as "0 files"."""
    body = _body(result=sample_review_result, prior_state=ReviewState())

    assert_that(body).does_not_contain("📁 Files reviewed")


# --- footer and size -------------------------------------------------------


def test_footer_delegates_the_other_two_surfaces(
    sample_review_result: ReviewResult,
) -> None:
    """The body ends by pointing at the surfaces it deliberately does not own."""
    body = _body(result=sample_review_result, prior_state=ReviewState())

    assert_that(body).ends_with(REVIEW_BODY_FOOTER)
    assert_that(body).contains("sticky comment")
    assert_that(body).contains("inline comments")


def test_long_file_lists_are_summarized_not_dumped(
    sample_review_result: ReviewResult,
) -> None:
    """A wide PR reports a file count rather than thousands of list items."""
    metadata = replace(
        sample_review_result.metadata,
        reviewed_paths=tuple(f"src/module_{index}.py" for index in range(5_000)),
    )
    body = _body(
        result=replace(sample_review_result, metadata=metadata),
        prior_state=ReviewState(),
    )

    assert_that(body).contains("…and 4940 more reviewed")
    assert_that(len(body)).is_less_than_or_equal_to(60_000)


def test_body_truncation_leaves_a_visible_marker(
    sample_review_result: ReviewResult,
) -> None:
    """Oversized content is cut with a marker, never silently."""
    finding = sample_review_result.findings[0]
    bulk = tuple(
        replace(finding, title=f"Finding {index}", line=index + 1)
        for index in range(4_000)
    )
    result = replace(sample_review_result, findings=bulk)
    match = match_findings(
        previous=ReviewState(),
        findings=bulk,
        round_number=1,
        head_sha="fb740b2aaa",
    )
    # One extra still-open record makes this round a strict subset of the open
    # set, so the full prompt panel renders instead of the sticky pointer.
    carried = FindingRecord(fingerprint="carried", title="Older finding")
    body = build_review_body(
        result=result,
        prior_state=_prior_state(rounds=1),
        match=replace(match, records=(*match.records, carried)),
        head_sha="fb740b2aaa",
    )

    assert_that(len(body)).is_less_than_or_equal_to(60_000)
    assert_that(body).ends_with("✂️ Comment truncated to fit GitHub's size limit.")


def test_skip_reasons_survive_a_full_reviewed_list(
    sample_review_result: ReviewResult,
) -> None:
    """A wide reviewed list must not crowd the skip reasons out of the body.

    Reviewed and skipped entries once shared one 60-slot budget, so a PR
    touching 60+ files rendered a skip *count* with no reason beside it —
    exactly the ambiguity this section exists to remove.
    """
    metadata = replace(
        sample_review_result.metadata,
        reviewed_paths=tuple(f"src/module_{index}.py" for index in range(200)),
        skipped_files=(
            SkippedFile(path="docs/README.md", reason=FileSkipReason.PATH_FILTER),
        ),
    )
    body = _body(
        result=replace(sample_review_result, metadata=metadata),
        prior_state=ReviewState(),
    )

    assert_that(body).contains(
        "- `docs/README.md` — skipped (outside the requested --path filter)",
    )
