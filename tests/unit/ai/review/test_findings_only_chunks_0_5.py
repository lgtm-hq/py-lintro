"""Small parallel findings-only chunks and the synthesis narrative (lintro-ops #37).

Milestone 0 step 0.5: every transport reviews in ``ai.review_chunk_diff_tokens``
chunks, a chunk answers with findings only, and the round's summary, verdict
reasoning and duplicate merges come from the synthesis pass.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import replace
from typing import Any, cast
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.ai.cli_schemas import SYNTHESIS_CLI_SCHEMA, cli_schema_for_synthesis
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.review.cli_limits import (
    REVIEW_CHUNK_DIFF_TOKEN_BUDGET,
    resolve_chunk_diff_budget,
)
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.run_planning import _resolve_diff_budget, resolve_review_chunks
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.synthesis import should_run_synthesis
from lintro.ai.review.synthesis_narrative import (
    DuplicateGroup,
    apply_duplicate_groups,
    finding_ids,
    parse_duplicate_groups,
    parse_synthesis_envelope,
)
from lintro.ai.token_budget import estimate_tokens
from lintro.config.review_config import ReviewSynthesisConfig


def _large_diff(*, files: int, lines_per_file: int) -> tuple[str, list[ChangedFile]]:
    """Build a multi-file unified diff large enough to need several chunks."""
    changed: list[ChangedFile] = []
    parts: list[str] = []
    pad = "payload_" + ("x" * 80)
    for index in range(files):
        path = f"src/module_{index}.py"
        changed.append(
            ChangedFile(
                path=path,
                status="modified",
                additions=lines_per_file,
                deletions=0,
            ),
        )
        hunk = "\n".join(
            f"+value_{index}_{row} = {row}  # {pad}" for row in range(lines_per_file)
        )
        parts.append(
            f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1,0 +1,{lines_per_file} @@\n{hunk}\n",
        )
    return "".join(parts), changed


def _context(*, unified_diff: str, changed: list[ChangedFile]) -> ReviewContext:
    """Wrap a diff in a review context."""
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=changed,
        unified_diff=unified_diff,
        pr_metadata=None,
        repo_root="/tmp/repo",
    )


def _options(*, transport: AITransport, **config: object) -> ReviewSessionOptions:
    """Build minimal session options for a budget resolution."""
    provider = MagicMock()
    provider.model_name = "test-model"
    provider.name = "test"
    return ReviewSessionOptions(
        provider=provider,
        ai_config=AIConfig(enabled=True, transport=transport, **config),  # type: ignore[arg-type]
        depth=1,
        checklist_items=[],
        checklist_text="",
        classifications=[],
    )


def _finding(*, file: str, line: int, severity: Severity, title: str) -> ReviewFinding:
    """Build a minimal finding."""
    return ReviewFinding(
        severity=severity,
        category="logic-bug",
        file=file,
        line=line,
        title=title,
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )


# --- section 1: chunk budget on every transport --------------------------------


def test_default_chunk_budget_is_seven_thousand_tokens() -> None:
    """The per-chunk budget defaults to 7k tokens on every transport."""
    assert_that(REVIEW_CHUNK_DIFF_TOKEN_BUDGET).is_equal_to(7_000)
    assert_that(AIConfig().review_chunk_diff_tokens).is_equal_to(7_000)


def test_chunk_budget_is_the_minimum_of_window_and_setting() -> None:
    """The budget never exceeds the configured chunk size or the window remainder."""
    assert_that(
        resolve_chunk_diff_budget(
            context_window_budget=150_000,
            review_chunk_diff_tokens=7_000,
        ),
    ).is_equal_to(7_000)
    assert_that(
        resolve_chunk_diff_budget(
            context_window_budget=2_000,
            review_chunk_diff_tokens=7_000,
        ),
    ).is_equal_to(2_000)


def test_a_thirty_k_token_diff_reviews_in_at_least_four_chunks_on_both_transports() -> (
    None
):
    """The chunker splits a 30k-token diff on the API transport as on the CLI."""
    unified_diff, changed = _large_diff(files=12, lines_per_file=110)
    assert_that(estimate_tokens(unified_diff)).is_greater_than(28_000)
    context = _context(unified_diff=unified_diff, changed=changed)

    for transport in (AITransport.API, AITransport.CLI):
        budget = _resolve_diff_budget(
            context=context,
            options=_options(transport=transport),
            context_window=200_000,
        )
        assert_that(budget).described_as(transport.value).is_equal_to(7_000)
        chunks = resolve_review_chunks(
            context=context,
            diff_budget=budget,
            classifications=[],
        )
        assert_that(len(chunks)).described_as(
            transport.value,
        ).is_greater_than_or_equal_to(4)
        for chunk in chunks:
            assert_that(estimate_tokens(chunk.diff)).is_less_than_or_equal_to(7_000)


def test_deprecated_cli_budget_alias_fills_the_new_key_and_warns() -> None:
    """``ai.cli_max_diff_tokens`` still works for one release, with a warning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = AIConfig(cli_max_diff_tokens=9_000)

    assert_that(config.review_chunk_diff_tokens).is_equal_to(9_000)
    messages = [str(item.message) for item in caught]
    assert_that(messages).is_not_empty()
    assert_that(messages[0]).contains("ai.review_chunk_diff_tokens")
    assert_that(messages[0]).contains("2026-10-15")


def test_explicit_new_key_wins_over_the_deprecated_alias() -> None:
    """Both keys set: the new spelling is the budget."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        config = AIConfig(cli_max_diff_tokens=9_000, review_chunk_diff_tokens=5_000)

    assert_that(config.review_chunk_diff_tokens).is_equal_to(5_000)


# --- section 3: synthesis narrative --------------------------------------------


def test_synthesis_runs_for_a_single_chunk_when_enabled() -> None:
    """The pass writes the round's summary, so one chunk is enough to run it."""
    config = ReviewSynthesisConfig()

    assert_that(config.enabled).is_true()
    assert_that(should_run_synthesis(config=config, chunks_reviewed=1)).is_true()
    assert_that(should_run_synthesis(config=config, chunks_reviewed=0)).is_false()
    assert_that(
        should_run_synthesis(
            config=ReviewSynthesisConfig(enabled=False),
            chunks_reviewed=3,
        ),
    ).is_false()


def test_synthesis_cli_schema_is_selected_on_the_cli_transport_only() -> None:
    """The CLI transport constrains the synthesis envelope natively."""
    request = cli_schema_for_synthesis(transport=AITransport.CLI)

    assert request is not None
    assert_that(request.schema).is_equal_to(SYNTHESIS_CLI_SCHEMA)
    assert_that(request.schema_name).is_equal_to("lintro_synthesis")
    assert_that(cli_schema_for_synthesis(transport=AITransport.API)).is_none()
    properties = cast(dict[str, Any], SYNTHESIS_CLI_SCHEMA["properties"])
    assert_that(set(properties)).is_equal_to(
        {"summary", "verdict_reasoning", "duplicates", "findings"},
    )


def test_synthesis_envelope_parses_summary_reasoning_and_duplicates() -> None:
    """The narrative half of the envelope parses into the existing models."""
    content = json.dumps(
        {
            "summary": {
                "headline": "Adds parallel chunks.",
                "walkthrough": [{"text": "Splits the diff.", "finding_ref": "a.py:3"}],
            },
            "verdict_reasoning": {
                "deciding_factor": "Nothing blocks the merge.",
                "failure_mechanism": "",
                "files_needing_attention": ["a.py"],
            },
            "duplicates": [
                {"keep": "a.py:3", "drop": ["b.py:9", "a.py:3"]},
                {"keep": "", "drop": ["b.py:9"]},
                "junk",
            ],
            "findings": [],
        },
    )

    narrative = parse_synthesis_envelope(content=content)

    assert narrative.summary is not None
    assert narrative.verdict_reasoning is not None
    assert_that(narrative.summary.headline).is_equal_to("Adds parallel chunks.")
    assert_that(narrative.summary.walkthrough[0].finding_ref).is_equal_to("a.py:3")
    assert_that(narrative.verdict_reasoning.files_needing_attention).is_equal_to(
        ("a.py",),
    )
    assert_that(narrative.duplicates).is_equal_to(
        (DuplicateGroup(keep="a.py:3", drop=("b.py:9",)),),
    )


def test_synthesis_envelope_degrades_on_unreadable_content() -> None:
    """Prose or a non-object answer leaves every narrative field empty."""
    for content in ("not json", "[1, 2]"):
        narrative = parse_synthesis_envelope(content=content)
        assert_that(narrative.summary).is_none()
        assert_that(narrative.verdict_reasoning).is_none()
        assert_that(narrative.duplicates).is_empty()
        assert_that(narrative.payload).is_none()
    assert_that(parse_duplicate_groups(raw_duplicates={"keep": "a"})).is_empty()


def test_duplicates_keep_the_highest_severity_and_absorb_the_sites() -> None:
    """The model's ``keep`` is advisory: the more severe side survives."""
    findings = (
        _finding(file="a.py", line=3, severity=Severity.P3, title="Missing guard"),
        _finding(file="b.py", line=9, severity=Severity.P1, title="Guard bypass"),
        _finding(file="c.py", line=1, severity=Severity.P2, title="Unrelated"),
    )

    kept, merged = apply_duplicate_groups(
        findings=findings,
        groups=(DuplicateGroup(keep="a.py:3", drop=("b.py:9",)),),
    )

    assert_that(merged).is_equal_to(1)
    assert_that([finding.title for finding in kept]).is_equal_to(
        ["Guard bypass", "Unrelated"],
    )
    survivor = kept[0]
    assert_that(
        [occurrence.label for occurrence in survivor.all_occurrences],
    ).is_equal_to(
        ["b.py:9", "a.py:3"],
    )


def test_duplicates_with_equal_severity_keep_the_earliest_finding() -> None:
    """Among equal severities the first reported finding survives."""
    findings = (
        _finding(file="a.py", line=3, severity=Severity.P2, title="First"),
        _finding(file="b.py", line=9, severity=Severity.P2, title="Second"),
    )

    kept, merged = apply_duplicate_groups(
        findings=findings,
        groups=(DuplicateGroup(keep="b.py:9", drop=("a.py:3",)),),
    )

    assert_that(merged).is_equal_to(1)
    assert_that(kept[0].title).is_equal_to("First")


def test_duplicates_resolve_by_digest_id() -> None:
    """Two findings at one location are told apart by their digest ids."""
    findings = (
        _finding(file="a.py", line=3, severity=Severity.P3, title="Missing guard"),
        _finding(file="a.py", line=3, severity=Severity.P2, title="Wrong default"),
        _finding(file="b.py", line=9, severity=Severity.P3, title="Guard again"),
    )
    assert_that(finding_ids(findings=findings)).is_equal_to(
        {
            ("a.py", 3, "Missing guard"): "F1",
            ("a.py", 3, "Wrong default"): "F2",
            ("b.py", 9, "Guard again"): "F3",
        },
    )

    kept, merged = apply_duplicate_groups(
        findings=findings,
        groups=(DuplicateGroup(keep="F1", drop=("F3",)),),
    )

    assert_that(merged).is_equal_to(1)
    assert_that([finding.title for finding in kept]).is_equal_to(
        ["Missing guard", "Wrong default"],
    )
    assert_that(
        [occurrence.label for occurrence in kept[0].all_occurrences],
    ).is_equal_to(["a.py:3", "b.py:9"])


def test_duplicates_ignore_an_ambiguous_location_and_an_unknown_id() -> None:
    """A ``file:line`` shared by two findings, or an id past the list, resolves to nothing."""
    findings = (
        _finding(file="a.py", line=3, severity=Severity.P3, title="Missing guard"),
        _finding(file="a.py", line=3, severity=Severity.P2, title="Wrong default"),
        _finding(file="b.py", line=9, severity=Severity.P3, title="Guard again"),
    )

    for group in (
        DuplicateGroup(keep="a.py:3", drop=("b.py:9",)),
        DuplicateGroup(keep="F9", drop=("F3",)),
    ):
        kept, merged = apply_duplicate_groups(findings=findings, groups=(group,))
        assert_that(merged).is_equal_to(0)
        assert_that(kept).is_equal_to(findings)


def test_duplicates_never_merge_a_question_with_a_finding() -> None:
    """A mixed-kind group is ignored; a question-only group still merges."""
    question = replace(
        _finding(file="a.py", line=3, severity=Severity.P1, title="Is this reachable?"),
        kind=FindingKind.QUESTION,
    )
    finding = _finding(file="b.py", line=9, severity=Severity.P2, title="Unreachable")
    kept, merged = apply_duplicate_groups(
        findings=(question, finding),
        groups=(DuplicateGroup(keep="F1", drop=("F2",)),),
    )
    assert_that(merged).is_equal_to(0)
    assert_that(kept).is_equal_to((question, finding))

    other_question = replace(
        _finding(file="c.py", line=1, severity=Severity.P3, title="Same question?"),
        kind=FindingKind.QUESTION,
    )
    kept, merged = apply_duplicate_groups(
        findings=(question, other_question),
        groups=(DuplicateGroup(keep="F1", drop=("F2",)),),
    )
    assert_that(merged).is_equal_to(1)
    assert_that(kept[0].title).is_equal_to("Is this reachable?")


def test_duplicates_with_an_unresolved_reference_are_ignored() -> None:
    """A group naming a location no finding reports changes nothing."""
    findings = (
        _finding(file="a.py", line=3, severity=Severity.P2, title="First"),
        _finding(file="b.py", line=9, severity=Severity.P2, title="Second"),
    )

    kept, merged = apply_duplicate_groups(
        findings=findings,
        groups=(DuplicateGroup(keep="a.py:3", drop=("z.py:1",)),),
    )

    assert_that(merged).is_equal_to(0)
    assert_that(kept).is_equal_to(findings)
