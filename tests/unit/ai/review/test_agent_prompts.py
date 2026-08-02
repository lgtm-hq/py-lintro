"""Tests for copyable AI-agent remediation prompt rendering."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.agent_prompts import (
    VERIFICATION_PREAMBLE,
    render_agent_prompt,
    render_agent_prompt_panel,
    render_finding_prompt,
    render_finding_prompt_panel,
    render_prompt_panel,
    render_sticky_prompt_pointer,
)
from lintro.ai.review.enums.agent_prompt_scope_kind import AgentPromptScopeKind
from lintro.ai.review.models.agent_prompt_scope import AgentPromptScope
from lintro.ai.review.models.review_finding import ReviewFinding, Severity


def _finding(
    *,
    file: str = "src/example.py",
    line: int = 10,
    severity: Severity = Severity.P1,
    category: str = "security",
    title: str = "Hardcoded password literal",
    description: str = "The password is assigned at module scope.",
    cause: str = "",
    fix: str = "Read it from the environment instead.",
) -> ReviewFinding:
    """Build a review finding for prompt-rendering tests.

    Args:
        file: Repository-relative file path.
        line: Line number in the file.
        severity: Finding severity.
        category: Finding category label.
        title: Short finding title.
        description: What is wrong and why it matters.
        cause: Root cause explanation.
        fix: Concise fix suggestion.

    Returns:
        A finding populated with the supplied values.
    """
    return ReviewFinding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        title=title,
        description=description,
        cause=cause,
        fix=fix,
        confidence="high",
    )


_ALL_OPEN = AgentPromptScope(
    kind=AgentPromptScopeKind.ALL_OPEN,
    round_number=3,
)
_THIS_REVIEW = AgentPromptScope(
    kind=AgentPromptScopeKind.THIS_REVIEW,
    round_number=3,
)


def test_preamble_text_is_verbatim() -> None:
    """The verification preamble matches the approved wording exactly."""
    assert_that(VERIFICATION_PREAMBLE).is_equal_to(
        "These are open findings from a lintro AI code review. Verify each one "
        "against the current code. Fix only still-valid issues, skip the rest "
        "with a brief reason, keep changes minimal, and validate with tests.",
    )


@pytest.mark.parametrize(
    "scope",
    [_ALL_OPEN, _THIS_REVIEW],
    ids=["scope=all_open", "scope=this_review"],
)
def test_prompt_contains_verbatim_preamble(scope: AgentPromptScope) -> None:
    """Both fix-all variants restate the verification preamble unchanged."""
    prompt = render_agent_prompt(findings=(_finding(),), scope=scope)
    assert_that(" ".join(prompt.split())).contains(VERIFICATION_PREAMBLE)


def test_single_finding_prompt_contains_verbatim_preamble() -> None:
    """The per-finding variant carries the same verification preamble."""
    prompt = render_finding_prompt(finding=_finding())
    assert_that(" ".join(prompt.split())).contains(VERIFICATION_PREAMBLE)


def test_all_open_scope_is_restated_on_the_first_line() -> None:
    """The sticky prompt names the cumulative scope before anything else."""
    prompt = render_agent_prompt(
        findings=(_finding(), _finding(line=8, severity=Severity.P2)),
        scope=_ALL_OPEN,
    )
    first = prompt.splitlines()[0]
    assert_that(first).starts_with("Scope: ALL 2 findings still open on this PR")
    assert_that(first).contains("after round 3")


def test_this_review_scope_is_restated_on_the_first_line() -> None:
    """The per-review prompt names the single-round scope before anything else."""
    prompt = render_agent_prompt(findings=(_finding(),), scope=_THIS_REVIEW)
    joined = " ".join(prompt.split())
    assert_that(prompt.splitlines()[0]).starts_with("Scope: the 1 finding posted in")
    assert_that(joined).contains("round 3 of this PR's lintro review ONLY")


def test_single_finding_scope_is_restated_on_the_first_line() -> None:
    """The inline prompt states that only one finding is in scope."""
    prompt = render_finding_prompt(finding=_finding())
    assert_that(prompt.splitlines()[0]).is_equal_to(
        "Scope: this single finding from a lintro AI code review.",
    )


def test_all_open_scope_sentence_uses_the_singular_form_for_one_finding() -> None:
    """A single still-open finding reads as `the 1 finding`, not `ALL 1`."""
    prompt = render_agent_prompt(findings=(_finding(),), scope=_ALL_OPEN)
    assert_that(prompt.splitlines()[0]).starts_with(
        "Scope: the 1 finding still open on this PR",
    )


def test_scope_lines_of_the_two_fix_all_variants_differ() -> None:
    """A copied fix-all prompt is never ambiguous about which set it covers."""
    findings = (_finding(),)
    sticky = render_agent_prompt(findings=findings, scope=_ALL_OPEN)
    per_review = render_agent_prompt(findings=findings, scope=_THIS_REVIEW)
    assert_that(sticky.splitlines()[0]).is_not_equal_to(per_review.splitlines()[0])


def test_round_one_all_open_scope_omits_the_round_suffix() -> None:
    """Round 1 has no earlier rounds to disambiguate against."""
    prompt = render_agent_prompt(
        findings=(_finding(),),
        scope=AgentPromptScope(
            kind=AgentPromptScopeKind.ALL_OPEN,
            round_number=1,
        ),
    )
    assert_that(prompt.splitlines()[0]).does_not_contain("after round")


def test_round_less_this_review_scope_names_the_latest_round() -> None:
    """Surfaces that do not track rounds still get an unambiguous scope line."""
    prompt = render_agent_prompt(
        findings=(_finding(),),
        scope=AgentPromptScope(kind=AgentPromptScopeKind.THIS_REVIEW),
    )
    assert_that(" ".join(prompt.split())).contains(
        "posted in the latest round of this PR's lintro review ONLY",
    )


def test_round_less_all_open_panel_title_omits_the_round_suffix() -> None:
    """Without a round number the sticky title carries no rounds parenthetical."""
    panel = render_agent_prompt_panel(
        findings=(_finding(), _finding(line=8)),
        scope=AgentPromptScope(kind=AgentPromptScopeKind.ALL_OPEN),
    )
    assert_that(panel.splitlines()[1]).is_equal_to(
        "> ⚡ **Fix-all prompt — all 2 still-open findings**",
    )


def test_single_open_finding_panel_title_uses_the_singular_form() -> None:
    """One still-open finding reads as `1 still-open finding`, not `all 1`."""
    panel = render_agent_prompt_panel(findings=(_finding(),), scope=_ALL_OPEN)
    assert_that(panel.splitlines()[1]).is_equal_to(
        "> ⚡ **Fix-all prompt — 1 still-open finding (rounds 1–3)**",
    )


def test_footers_cover_every_scope_kind() -> None:
    """Every scope kind renders a default footer instead of raising."""
    expected_substrings = {
        AgentPromptScopeKind.ALL_OPEN: "Regenerated every run",
        AgentPromptScopeKind.THIS_REVIEW: "sticky comment's fix-all prompt",
        AgentPromptScopeKind.SINGLE_FINDING: "Paste into Claude Code",
    }
    for kind in AgentPromptScopeKind:
        panel = render_agent_prompt_panel(
            findings=(_finding(),),
            scope=AgentPromptScope(kind=kind),
        )
        assert_that(panel).contains("<sub>")
        assert_that(panel).contains(expected_substrings[kind])


def test_findings_are_grouped_by_file_in_first_seen_order() -> None:
    """Each file gets one header and keeps the caller's ordering."""
    findings = (
        _finding(file="src/b.py", line=10),
        _finding(file="src/a.py", line=4),
        _finding(file="src/b.py", line=2),
    )
    prompt = render_agent_prompt(findings=findings, scope=_ALL_OPEN)
    assert_that(prompt.count("In `src/b.py`:")).is_equal_to(1)
    assert_that(prompt.index("In `src/b.py`:")).is_less_than(
        prompt.index("In `src/a.py`:"),
    )
    assert_that(prompt.index("- Line 10 —")).is_less_than(prompt.index("- Line 2 —"))


def test_finding_bullet_carries_severity_and_category() -> None:
    """The bullet line follows the approved `Line N — **Title** (P? · cat):` shape."""
    prompt = render_agent_prompt(
        findings=(_finding(line=42, severity=Severity.P2, category="logic-bug"),),
        scope=_ALL_OPEN,
    )
    assert_that(prompt).contains(
        "- Line 42 — **Hardcoded password literal** (P2 · logic-bug):",
    )


def test_reasoning_and_fix_are_indented_continuation_lines() -> None:
    """Continuation lines sit under the bullet, indented by two spaces."""
    prompt = render_agent_prompt(
        findings=(
            _finding(
                description="Secret in source.",
                cause="It was inlined for a demo.",
                fix="Read it from the environment.",
            ),
        ),
        scope=_ALL_OPEN,
    )
    lines = prompt.splitlines()
    bullet_index = next(i for i, line in enumerate(lines) if line.startswith("- Line"))
    continuation_lines = [
        line
        for line in lines[bullet_index + 1 :]
        if line.startswith("  ") and not line.startswith("- ")
    ]
    reasoning_line = next(
        line for line in continuation_lines if not line.startswith("  Fix:")
    )
    fix_line = next(line for line in continuation_lines if line.startswith("  Fix:"))
    assert_that(reasoning_line).is_equal_to(
        "  Secret in source. It was inlined for a demo.",
    )
    assert_that(fix_line).is_equal_to(
        "  Fix: Read it from the environment.",
    )


def test_empty_cause_and_fix_are_omitted() -> None:
    """Findings without a cause or fix render only the reasoning line."""
    prompt = render_agent_prompt(
        findings=(_finding(cause="", fix=""),),
        scope=_ALL_OPEN,
    )
    assert_that(prompt).does_not_contain("Fix:")


def test_no_findings_render_no_prompt() -> None:
    """An empty finding set produces no prompt at all."""
    assert_that(render_agent_prompt(findings=(), scope=_ALL_OPEN)).is_empty()


def test_no_findings_render_no_panel() -> None:
    """An empty finding set produces no panel, not an empty shell."""
    assert_that(render_agent_prompt_panel(findings=(), scope=_ALL_OPEN)).is_empty()


def test_blank_prompt_renders_no_panel() -> None:
    """A whitespace-only prompt never yields a panel."""
    assert_that(render_prompt_panel(prompt="   \n", title="x")).is_empty()


def test_panel_structure_is_an_alert_with_a_collapsed_fenced_prompt() -> None:
    """The panel is an IMPORTANT alert, header visible, prompt fenced inside details."""
    panel = render_agent_prompt_panel(findings=(_finding(),), scope=_ALL_OPEN)
    lines = panel.splitlines()
    assert_that(lines[0]).is_equal_to("> [!IMPORTANT]")
    assert_that(lines[1]).starts_with("> ⚡ **")
    assert_that(lines[2]).is_equal_to(">")
    assert_that(lines[3]).is_equal_to("> <details><summary>Show prompt</summary>")
    assert_that(lines[4]).is_equal_to(">")
    assert_that(lines[5]).is_equal_to("> ```")
    assert_that(panel).contains("> </details>")
    assert_that([line for line in lines if not line.startswith(">")]).is_empty()


def test_panel_body_lines_stay_inside_the_blockquote() -> None:
    """Every prompt line is quoted so the alert is not broken mid-body."""
    panel = render_agent_prompt_panel(
        findings=(_finding(file="src/a.py"), _finding(file="src/b.py")),
        scope=_ALL_OPEN,
    )
    assert_that(all(line.startswith(">") for line in panel.splitlines())).is_true()


def test_panel_blank_lines_are_bare_quote_markers() -> None:
    """Blank prompt lines render as `>` with no trailing whitespace."""
    panel = render_agent_prompt_panel(findings=(_finding(),), scope=_ALL_OPEN)
    assert_that([line for line in panel.splitlines() if line == "> "]).is_empty()
    assert_that(panel.splitlines()).contains(">")


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (_ALL_OPEN, "> ⚡ **Fix-all prompt — all 2 still-open findings (rounds 1–3)**"),
        (_THIS_REVIEW, "> ⚡ **Fix prompt — this round's 2 findings only**"),
    ],
    ids=["scope=all_open", "scope=this_review"],
)
def test_panel_title_states_scope_and_finding_count(
    scope: AgentPromptScope,
    expected: str,
) -> None:
    """The visible header names both the scope and how many findings it covers."""
    panel = render_agent_prompt_panel(
        findings=(_finding(), _finding(line=8)),
        scope=scope,
    )
    assert_that(panel.splitlines()[1]).is_equal_to(expected)


def test_single_finding_panel_title_is_the_agent_prompt_label() -> None:
    """Inline panels use the short `Prompt for AI agents` header."""
    panel = render_finding_prompt_panel(finding=_finding())
    assert_that(panel.splitlines()[1]).is_equal_to("> ⚡ **Prompt for AI agents**")


def test_panel_footer_defaults_to_the_scope_footer() -> None:
    """The per-review panel points readers at the sticky for the full set."""
    panel = render_agent_prompt_panel(findings=(_finding(),), scope=_THIS_REVIEW)
    assert_that(panel).contains(
        "For everything still open across all rounds, use the sticky comment's",
    )


def test_panel_footer_can_be_suppressed() -> None:
    """Passing an empty footer omits the small-print line entirely."""
    panel = render_agent_prompt_panel(
        findings=(_finding(),),
        scope=_THIS_REVIEW,
        footer="",
    )
    assert_that(panel).does_not_contain("<sub>")


def test_panel_footer_can_be_overridden() -> None:
    """Consumers can supply a surface-specific footer, e.g. a sticky link."""
    panel = render_agent_prompt_panel(
        findings=(_finding(),),
        scope=_THIS_REVIEW,
        footer="See the [sticky](https://example.test/c/1)",
    )
    assert_that(panel).contains("<sub>See the [sticky](https://example.test/c/1)</sub>")


def test_fence_is_widened_when_the_prompt_contains_triple_backticks() -> None:
    """A finding quoting a fenced block cannot close the prompt's own fence."""
    panel = render_agent_prompt_panel(
        findings=(_finding(description="Wrap it in ``` fences ```."),),
        scope=_ALL_OPEN,
    )
    assert_that(panel.splitlines()[5]).is_equal_to("> ````")
    assert_that(panel.splitlines()).contains("> ````")


def test_fence_outgrows_the_longest_backtick_run() -> None:
    """The fence is always at least one backtick longer than any internal run."""
    panel = render_agent_prompt_panel(
        findings=(_finding(fix="Use ````` five ````` backticks."),),
        scope=_ALL_OPEN,
    )
    assert_that(panel.splitlines()[5]).is_equal_to("> ``````")


def test_single_finding_prompt_matches_the_one_finding_fix_all_body() -> None:
    """The per-finding variant is the shared template with a single block."""
    finding = _finding()
    prompt = render_finding_prompt(finding=finding)
    assert_that(prompt).contains("In `src/example.py`:")
    assert_that(prompt.count("- Line ")).is_equal_to(1)


@pytest.mark.parametrize(
    ("description", "broken", "preserved"),
    [
        ("Reported by @octocat.", "@octocat", ""),
        ("Owned by @lgtm-hq/reviewers.", "@lgtm-hq", ""),
        ("Ping @octocat and @hubot.", "@hubot", ""),
        ("Raised by @octocat; mail dev@example.test.", "@octocat", "dev@example.test"),
    ],
    ids=[
        "case=user_mention",
        "case=team_mention",
        "case=multiple_mentions",
        "case=mention_with_email",
    ],
)
def test_prompt_text_is_sanitized_against_mentions(
    description: str,
    broken: str,
    preserved: str,
) -> None:
    """Untrusted model text cannot ping GitHub users from a prompt panel.

    Args:
        description: Finding description carrying the untrusted text.
        broken: Mention that must not survive rendering verbatim.
        preserved: Non-mention text that must survive untouched, if any.
    """
    prompt = render_agent_prompt(
        findings=(_finding(description=description),),
        scope=_ALL_OPEN,
    )
    assert_that(prompt).does_not_contain(broken)
    if preserved:
        assert_that(prompt).contains(preserved)


def test_negative_round_number_is_rejected() -> None:
    """A scope cannot claim a round that no review could have produced."""
    with pytest.raises(ValueError, match="round_number must be >= 1"):
        AgentPromptScope(kind=AgentPromptScopeKind.ALL_OPEN, round_number=0)


def test_sticky_pointer_links_back_instead_of_duplicating_the_prompt() -> None:
    """When both scopes coincide the per-review body links to the sticky."""
    pointer = render_sticky_prompt_pointer(sticky_url="https://example.test/c/1")
    assert_that(pointer).contains(
        "[sticky comment's fix-all](https://example.test/c/1)",
    )
    assert_that(pointer).does_not_contain("```")
