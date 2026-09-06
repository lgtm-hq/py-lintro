"""Mock-parity gaps closed after the #1905 comment-surfaces audit (#1951).

Each test here pins one behaviour the rendered surfaces were missing relative
to the epic's mocks: linked finding titles, an honest run-history table, a
per-round narrative, a regression that says it is one, config-excluded files in
the skipped list, the verdict rubric under the pill, and the highlighted mode-B
fix.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.models.github_api_response import GitHubApiResponse
from lintro.ai.review.enums.file_skip_reason import (
    FileSkipReason,
    describe_skip_reason,
)
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import fingerprint_for
from lintro.ai.review.github import post_review_to_github
from lintro.ai.review.github_constants import STATE_MARKER_PREFIX, STICKY_MARKER
from lintro.ai.review.github_render import (
    REGRESSED_TITLE_SUFFIX,
    format_finding_comment,
)
from lintro.ai.review.github_review_body import REVIEW_BODY_FOOTER
from lintro.ai.review.inline_fix import plan_inline_fix
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.models.suggested_change import SuggestedChange
from lintro.ai.review.review_state_codec import leftover_state_block
from lintro.ai.review.sticky import advance_review_state, build_sticky_comment
from lintro.ai.review.verdict import VERDICT_RUBRIC_FINE_PRINT


def _finding(
    *,
    title: str = "Leak",
    severity: Severity = Severity.P1,
    line: int = 10,
    file: str = "src/main.py",
) -> ReviewFinding:
    """Build a review finding for the parity tests."""
    return ReviewFinding(
        severity=severity,
        category="security",
        file=file,
        line=line,
        title=title,
        description="Stores an application password as a module-level literal.",
        cause="Assigned at module scope.",
        fix="Read it from the environment.",
        confidence="high",
    )


def _with(
    *,
    base: ReviewResult,
    findings: tuple[ReviewFinding, ...],
    **overrides: Any,
) -> ReviewResult:
    """Return a copy of ``base`` carrying different findings and fields."""
    return replace(base, findings=findings, **overrides)


def _body_only(*, body: str) -> str:
    """Strip the hidden state blob so assertions only see rendered Markdown."""
    return body.split(STATE_MARKER_PREFIX, 1)[0]


# --- 1. open findings link to their inline comment ---------------------------


def test_open_finding_title_links_to_its_inline_comment(
    sample_review_result: ReviewResult,
) -> None:
    """A finding whose thread is known renders its title as a link to it."""
    result = _with(base=sample_review_result, findings=(_finding(),))
    state = advance_review_state(request=StickyRequest(result=result, head_sha="sha1"))
    key = state.findings[0].key

    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=result,
                prior_state=state,
                head_sha="sha1",
                inline_comment_ids={key: 424242},
                repo="owner/name",
                pr_number=7,
            ),
        ),
    )

    assert_that(body).contains(
        "[Leak](https://github.com/owner/name/pull/7#discussion_r424242)",
    )


def test_open_finding_without_a_comment_id_renders_unlinked(
    sample_review_result: ReviewResult,
) -> None:
    """No thread means no link — never a dead one."""
    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=_with(base=sample_review_result, findings=(_finding(),)),
                head_sha="sha1",
                repo="owner/name",
                pr_number=7,
            ),
        ),
    )

    assert_that(body).does_not_contain("#discussion_r")
    assert_that(body).contains("| Leak ")


def test_open_finding_renders_unlinked_without_repo_context(
    sample_review_result: ReviewResult,
) -> None:
    """A known comment id is not enough: the URL also needs repo and PR."""
    result = _with(base=sample_review_result, findings=(_finding(),))
    state = advance_review_state(request=StickyRequest(result=result))

    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=result,
                prior_state=state,
                inline_comment_ids={state.findings[0].key: 99},
            ),
        ),
    )

    assert_that(body).does_not_contain("#discussion_r")


def test_a_model_written_bracket_cannot_break_out_of_the_link(
    sample_review_result: ReviewResult,
) -> None:
    """A title is untrusted text, and it is the link's label.

    Without neutralized brackets a title like ``Fix][evil](http://phish)``
    closes the label early and injects an attacker-chosen URL into the sticky
    comment, so the guard is load-bearing rather than cosmetic.
    """
    hostile = "Fix][evil](https://phishing.example"
    result = _with(
        base=sample_review_result,
        findings=(_finding(title=hostile),),
    )
    state = advance_review_state(request=StickyRequest(result=result))

    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=result,
                prior_state=state,
                inline_comment_ids={state.findings[0].key: 424242},
                repo="owner/name",
                pr_number=7,
            ),
        ),
    )

    row = next(line for line in body.splitlines() if "discussion_r424242" in line)

    assert_that(row).contains(
        "[Fix)(evil)(https://phishing.example]"
        "(https://github.com/owner/name/pull/7#discussion_r424242)",
    )
    # The label closes exactly once, at the real thread URL. (The raw title
    # still appears verbatim inside the fix-all prompt's fenced code block,
    # where Markdown renders it as literal text rather than as a link.)
    assert_that(row).does_not_contain("](https://phishing.example")


# --- 2. run history: fixed count and still-open count ------------------------


def test_history_row_reports_open_after_the_round_and_what_it_fixed(
    sample_review_result: ReviewResult,
) -> None:
    """Round two fixed one finding and left one open; the table says so."""
    first_result = _with(
        base=sample_review_result,
        findings=(_finding(title="Leak"), _finding(title="Race", line=20)),
    )
    prior = advance_review_state(
        request=StickyRequest(result=first_result, head_sha="sha1"),
    )
    second = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=_with(
                    base=sample_review_result,
                    findings=(_finding(title="Leak"),),
                ),
                prior_state=prior,
                head_sha="sha2",
            ),
        ),
    )

    assert_that(second).contains("1 open · 1 fixed this round")
    assert_that(second).contains("| ✔ fixed | 🔴 P1 | ~~Race~~ |")


def test_history_row_falls_back_for_state_without_the_new_counts(
    sample_review_result: ReviewResult,
) -> None:
    """A record persisted before the counts existed renders raised and ``—``."""
    prior_state = ReviewState(
        runs=(RunRecord(round=1, sha="sha1", model="m", p1=2, p2=1),),
    )

    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=_with(base=sample_review_result, findings=()),
                prior_state=prior_state,
                head_sha="sha2",
            ),
        ),
    )

    # Legacy round 1: three findings raised (p1+p2), fixed count treated as 0.
    assert_that(body).contains("<b>Round 1</b>")
    assert_that(body).contains("3 left open")


def test_legacy_run_payload_without_the_new_fields_loads() -> None:
    """A v1/v2 payload parses with the new fields absent, not zeroed."""
    record = RunRecord.from_dict({"round": 1, "sha": "sha1", "p1": 2})

    assert_that(record.resolved).is_none()
    assert_that(record.open_after).is_none()
    assert_that(record.narrative).is_equal_to("")
    assert_that(record.to_dict()).does_not_contain_key(
        "resolved",
        "open_after",
        "narrative",
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("inf", id="non-finite"),
        pytest.param("not-a-number", id="garbage"),
        pytest.param(True, id="bool"),
    ],
)
def test_a_corrupted_count_decodes_as_unknown(value: object) -> None:
    """A malformed blob renders "unknown", and never aborts the decode."""
    record = RunRecord.from_dict({"round": 1, "resolved": value})

    assert_that(record.resolved).is_none()


def test_new_run_fields_round_trip_through_the_state_blob() -> None:
    """The counts and narrative survive serialization."""
    record = RunRecord(
        round=2,
        resolved=3,
        open_after=1,
        narrative="Fixed the fail-open default.",
    )

    restored = RunRecord.from_dict(record.to_dict())

    assert_that(restored.resolved).is_equal_to(3)
    assert_that(restored.open_after).is_equal_to(1)
    assert_that(restored.narrative).is_equal_to("Fixed the fail-open default.")


# --- 3. per-round narrative --------------------------------------------------


def test_history_recap_renders_the_rounds_narrative(
    sample_review_result: ReviewResult,
) -> None:
    """The model's own account of a round beats a severity tally."""
    first_result = _with(
        base=sample_review_result,
        findings=(_finding(),),
        pr_summary=ReviewSummary(
            headline="Adds a fail-open default to the auth path.",
            walkthrough=(),
        ),
    )
    prior = advance_review_state(
        request=StickyRequest(result=first_result, head_sha="sha1"),
    )
    second = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=_with(base=sample_review_result, findings=(_finding(),)),
                prior_state=prior,
                head_sha="sha2",
            ),
        ),
    )

    assert_that(second).contains("<b>Round 1</b>")
    assert_that(second).contains("Adds a fail-open default to the auth path.")


def test_history_recap_falls_back_to_counts_without_a_narrative(
    sample_review_result: ReviewResult,
) -> None:
    """A legacy record has no narrative, so the counts line stands in."""
    prior_state = ReviewState(
        runs=(RunRecord(round=1, sha="sha1", model="m", p1=1, p2=2, p3=3),),
    )

    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=_with(base=sample_review_result, findings=()),
                prior_state=prior_state,
                head_sha="sha2",
            ),
        ),
    )

    assert_that(body).contains("<b>Round 1</b>")
    assert_that(body).contains("6 left open")


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        pytest.param(
            "One sentence. And a second that must not be stored.",
            "One sentence.",
            id="period",
        ),
        pytest.param(
            "Is this intentional? A second sentence follows.",
            "Is this intentional?",
            id="question-mark",
        ),
        pytest.param(
            "It fails outright! A second sentence follows.",
            "It fails outright!",
            id="exclamation-mark",
        ),
        pytest.param(
            "First sentence.\nSecond sentence.",
            "First sentence.",
            id="newline-boundary",
        ),
        pytest.param("No terminator at all", "No terminator at all", id="no-boundary"),
    ],
)
def test_narrative_keeps_only_the_first_sentence(
    sample_review_result: ReviewResult,
    summary: str,
    expected: str,
) -> None:
    """A recap is one line; the rest of a paragraph is not persisted."""
    result = _with(base=sample_review_result, findings=(), summary=summary)
    stored = advance_review_state(
        request=StickyRequest(result=result, head_sha="sha1"),
    ).runs[-1]

    assert_that(stored.narrative).is_equal_to(expected)


# --- 4. a regression says it is one ------------------------------------------


def _resolved_state(*, finding: ReviewFinding) -> ReviewState:
    """Build prior state in which ``finding`` was raised and already fixed."""
    return ReviewState(
        runs=(RunRecord(round=1, sha="sha1", model="m"),),
        findings=(
            FindingRecord(
                fingerprint=fingerprint_for(
                    file=finding.file,
                    category=finding.category,
                    title=finding.title,
                ),
                severity=finding.severity,
                category=finding.category,
                title=finding.title,
                file=finding.file,
                line=finding.line,
                status=FindingStatus.RESOLVED,
                since_round=1,
                resolved_sha="sha1",
                resolved_round=1,
                inline_comment_id=11,
            ),
        ),
    )


def test_regressed_thread_titles_say_regressed(
    sample_review_result: ReviewResult,
) -> None:
    """The fresh thread's title carries the suffix, not just a provenance note."""
    finding = _finding()
    prior_body = STICKY_MARKER + leftover_state_block(
        state=_resolved_state(finding=finding),
    )
    reporter = MagicMock()
    reporter.is_available.return_value = True
    reporter.find_issue_comment.return_value = (5, prior_body)
    reporter.fetch_pr_diff_lines.return_value = {"src/main.py": {10}}
    reporter.fetch_compare_lines.return_value = {"src/main.py": {10}}
    reporter.fetch_pr_commit_shas.return_value = []
    reporter.fetch_review_comments.return_value = []
    reporter.update_issue_comment.return_value = True
    reporter.api_response.return_value = GitHubApiResponse(status=200)
    reporter.api_base = "https://api.github.com"
    reporter.repo = "owner/name"
    reporter.pr_number = 7

    posted = post_review_to_github(
        result=_with(base=sample_review_result, findings=(finding,)),
        reporter=reporter,
    )

    review_calls = [
        call
        for call in reporter.api_response.call_args_list
        if len(call.args) == 3 and str(call.args[1]).endswith("/reviews")
    ]
    assert_that(posted).is_true()
    assert_that(review_calls).is_length(1)
    assert_that(review_calls[0].args[2]["comments"][0]["body"]).contains(
        f"**Leak{REGRESSED_TITLE_SUFFIX}**",
    )


def test_the_regressed_suffix_lands_inside_the_bold_title() -> None:
    """Unit-level pin for the contract the posting test exercises end to end."""
    body = format_finding_comment(
        finding=_finding(),
        title_suffix=REGRESSED_TITLE_SUFFIX,
    )

    assert_that(body).contains("**Leak (regressed)**")


def test_a_fresh_finding_title_carries_no_suffix() -> None:
    """Only a regression is labelled; a new finding reads as itself."""
    body = format_finding_comment(finding=_finding())

    assert_that(body).contains("**Leak**")
    assert_that(body).does_not_contain(REGRESSED_TITLE_SUFFIX)


# --- 5. config-excluded files are reported as skipped ------------------------
#
# The filtering itself is exercised end to end through ``collect_review_context``
# in ``test_context_collect.py``; only the rendered wording is pinned here.


def test_config_excluded_reason_reads_as_a_configured_choice() -> None:
    """The rendered phrase must not read as a coverage gap."""
    assert_that(
        describe_skip_reason(reason=FileSkipReason.CONFIG_EXCLUDED),
    ).is_equal_to("excluded by config (ai.exclude_paths)")


# --- 6. the verdict explainer sits under the pill ----------------------------


@pytest.mark.parametrize(
    ("findings", "verdict"),
    [
        pytest.param((), ReviewVerdict.READY, id="ready"),
        pytest.param((_finding(),), ReviewVerdict.BLOCKED, id="blocked"),
    ],
)
def test_verdict_explainer_renders_on_every_round(
    sample_review_result: ReviewResult,
    findings: tuple[ReviewFinding, ...],
    verdict: ReviewVerdict,
) -> None:
    """The title carries the derived verdict on every round."""
    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=_with(base=sample_review_result, findings=findings),
            ),
        ),
    )
    labels = {
        ReviewVerdict.READY: "✅ Ready",
        ReviewVerdict.BLOCKED: "⛔ Blocked",
    }

    assert_that(body).contains(f"## 🔎 Lintro Review — {labels[verdict]}")


def test_verdict_explainer_sits_directly_under_the_pill(
    sample_review_result: ReviewResult,
) -> None:
    """The mockup puts the derived verdict in the title, not a separate pill."""
    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=_with(base=sample_review_result, findings=(_finding(),)),
            ),
        ),
    )

    assert_that(body).contains("## 🔎 Lintro Review — ⛔ Blocked")


def test_verdict_rubric_reads_in_the_mock_style() -> None:
    """The rubric uses severity dots and arrows, not prose semicolons."""
    assert_that(VERDICT_RUBRIC_FINE_PRINT).is_equal_to(
        "Verdict is derived: open 🔴 P1 → Blocked · else open 🟠 P2 → Changes "
        "requested · else 🟡 P3 → Nits only · else ✅ Ready.",
    )


# --- 7. wording --------------------------------------------------------------


def test_review_body_footer_points_below_for_finding_detail() -> None:
    """The inline comments sit under the body, and the footer says so."""
    assert_that(REVIEW_BODY_FOOTER).contains("finding details → inline comments below")


def test_fix_all_panel_caption_names_the_open_table(
    sample_review_result: ReviewResult,
) -> None:
    """The panel covers the table right above it, which is what it now says."""
    body = _body_only(
        body=build_sticky_comment(
            request=StickyRequest(
                result=_with(base=sample_review_result, findings=(_finding(),)),
            ),
        ),
    )

    assert_that(body).contains(
        "Regenerated every run · covers exactly the open table above",
    )


# --- 8. mode B renders the fix as a highlighted line -------------------------


def test_mode_b_fix_renders_as_a_tip_callout() -> None:
    """Without a committable suggestion the described fix is highlighted."""
    finding = _finding(severity=Severity.P3)

    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(finding=finding, round_diff_lines=None),
    )

    assert_that(body).contains("> [!TIP]\n> **Fix:** Read it from the environment.")
    # The prompt panel owns [!IMPORTANT]; the two alerts must not collide.
    assert_that(body).does_not_contain("> [!TIP]\n> [!IMPORTANT]")


def test_mode_a_keeps_the_committable_suggestion_block() -> None:
    """A committable change still wins the fix slot over the tip callout."""
    finding = replace(
        _finding(),
        suggested_change=SuggestedChange(
            start_line=10,
            end_line=10,
            replacement='password = os.environ["APP_PASSWORD"]',
        ),
    )
    plan = plan_inline_fix(
        finding=finding,
        round_diff_lines={"src/main.py": {10}},
    )

    body = format_finding_comment(finding=finding, inline_fix=plan)

    assert_that(body).contains("```suggestion")
    assert_that(body).does_not_contain("> [!TIP]")
