"""Prompt-bytes goldens for the review prompt builders (issue #2298).

`build_review_prompt` and `build_git_native_review_prompt` had no byte-level
coverage over a fixed context before this suite. The #1972 decomposition
(#2299-#2302) is behaviour-preserving only if these files stay byte-identical.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.review.cli_limits import resolve_cli_findings_cap
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.orchestrator import (
    build_git_native_review_prompt,
    build_review_prompt,
)
from lintro.ai.review.paths_registry import generate_interaction_paths
from lintro.ai.review.sensitivity import (
    format_strictness_prompt_section,
    resolve_sensitivity_policy,
)
from tests.unit.ai.review.golden.golden_fixtures import (
    FAKE_SECRET_LINE,
    GOLDEN_BOUNDARY,
    golden_checklist_text,
    golden_chunks,
    golden_classifications,
    golden_review_context,
)
from tests.unit.ai.review.golden.golden_io import assert_golden

_CHECKLIST_COUNT = 2
_INTERACTION_PATHS = "- session status -> token decoding"
_LINT_DIGEST = "ruff: 1 issue in src/auth/session.py"
_STRICTNESS = "Report only defects you can trace to a concrete failure."


def _production_strictness_section() -> str:
    """Return the strictness text ``run_review`` injects by default.

    Returns:
        The balanced-preset prompt section, straight from production code.
    """
    return format_strictness_prompt_section(
        policy=resolve_sensitivity_policy(strictness=ReviewStrictness.BALANCED),
    )


def _production_interaction_paths() -> str:
    """Return the interaction paths production generates for the fixture.

    Returns:
        Domain-triggered path text for the fixture's classifications.
    """
    context = golden_review_context()
    return generate_interaction_paths(
        classifications=golden_classifications(),
        changed_files=[file.path for file in context.changed_files],
    )


@pytest.fixture(autouse=True)
def _pin_boundary_marker() -> Iterator[None]:
    """Pin the random prompt boundary marker for the whole module.

    ``make_boundary_marker`` mixes a UUID suffix in by design, so no prompt
    golden can exist without pinning it. Nothing below ``call_ai`` is patched.

    Yields:
        None: The patch is active for the test body.
    """
    with patch(
        "lintro.ai.review.orchestrator.make_boundary_marker",
        return_value=GOLDEN_BOUNDARY,
    ):
        yield


@pytest.mark.parametrize("chunk_index", [0, 1])
def test_build_review_prompt_matches_golden(chunk_index: int) -> None:
    """The API-transport prompt for each fixed chunk matches its golden.

    Args:
        chunk_index: Index into the fixed two-chunk plan.
    """
    chunk = golden_chunks()[chunk_index]
    system_prompt, user_prompt = build_review_prompt(
        chunk=chunk,
        context=golden_review_context(),
        checklist_text=golden_checklist_text(),
        checklist_count=_CHECKLIST_COUNT,
        interaction_paths=_INTERACTION_PATHS,
        lint_results=_LINT_DIGEST,
        strictness_section=_STRICTNESS,
        max_findings=10,
    )

    assert_golden(name="prompt_system.golden", actual=system_prompt)
    assert_golden(
        name=f"prompt_user_chunk_{chunk.id}.golden",
        actual=user_prompt,
    )


@pytest.mark.parametrize("chunk_index", [0, 1])
def test_build_git_native_review_prompt_matches_golden(chunk_index: int) -> None:
    """The CLI-transport prompt for each fixed chunk matches its golden.

    Args:
        chunk_index: Index into the fixed two-chunk plan.
    """
    chunk = golden_chunks()[chunk_index]
    system_prompt, user_prompt = build_git_native_review_prompt(
        chunk=chunk,
        context=golden_review_context(),
        checklist_text=golden_checklist_text(),
        checklist_count=_CHECKLIST_COUNT,
        interaction_paths=_INTERACTION_PATHS,
        lint_results=_LINT_DIGEST,
        strictness_section=_STRICTNESS,
        max_findings=10,
    )

    assert_golden(name="prompt_system.golden", actual=system_prompt)
    assert_golden(
        name=f"prompt_git_native_user_chunk_{chunk.id}.golden",
        actual=user_prompt,
    )


def test_git_native_delegated_diff_command_matches_golden() -> None:
    """The opt-in delegated ``git diff`` prompt keeps its exact shape.

    This is the one path that does not embed a redacted diff, so its bytes are
    pinned separately from the default embedded path.
    """
    _, user_prompt = build_git_native_review_prompt(
        chunk=golden_chunks()[1],
        context=golden_review_context(),
        checklist_text=golden_checklist_text(),
        checklist_count=_CHECKLIST_COUNT,
        interaction_paths=_INTERACTION_PATHS,
        lint_results=_LINT_DIGEST,
        strictness_section=_STRICTNESS,
        embed_diff=False,
        allow_unredacted_git_native=True,
    )

    assert_golden(name="prompt_git_native_delegated_chunk_2.golden", actual=user_prompt)


def test_prompt_goldens_prove_the_redaction_choke_point_fired() -> None:
    """No prompt builder may emit the fixture's secret verbatim.

    Redaction is an architecture invariant (ADR-0008), not a formatting
    detail, so it is asserted directly rather than being implied by the
    golden bytes.
    """
    chunk = golden_chunks()[0]
    context = golden_review_context()
    _, api_prompt = build_review_prompt(
        chunk=chunk,
        context=context,
        checklist_text=golden_checklist_text(),
        checklist_count=_CHECKLIST_COUNT,
        interaction_paths=_INTERACTION_PATHS,
    )
    _, cli_prompt = build_git_native_review_prompt(
        chunk=chunk,
        context=context,
        checklist_text=golden_checklist_text(),
        checklist_count=_CHECKLIST_COUNT,
        interaction_paths=_INTERACTION_PATHS,
    )

    for prompt in (api_prompt, cli_prompt):
        assert_that(prompt).does_not_contain(FAKE_SECRET_LINE)
        assert_that(prompt).contains("[REDACTED]")


def test_api_prompt_with_production_defaults_matches_golden() -> None:
    """The API-transport prompt built the way ``run_review`` builds it is pinned.

    The other prompt goldens call the builders as pure functions with stand-in
    knobs. This one feeds them the values production actually passes for the
    API transport — an uncapped findings rule from ``resolve_cli_findings_cap``,
    the balanced strictness section, generated interaction paths, and no lint
    digest — so a change to any of those defaults reddens a golden.
    """
    _, user_prompt = build_review_prompt(
        chunk=golden_chunks()[0],
        context=golden_review_context(),
        checklist_text=golden_checklist_text(),
        checklist_count=_CHECKLIST_COUNT,
        interaction_paths=_production_interaction_paths(),
        lint_results=None,
        strictness_section=_production_strictness_section(),
        max_findings=resolve_cli_findings_cap(
            transport_is_cli=False,
            cli_max_findings_per_call=AIConfig().cli_max_findings_per_call,
        ),
    )

    assert_golden(name="prompt_user_chunk_1_production_api.golden", actual=user_prompt)


def test_cli_prompt_with_production_defaults_matches_golden() -> None:
    """The CLI-transport prompt built the way ``run_review`` builds it is pinned.

    The findings cap comes from ``AIConfig.cli_max_findings_per_call`` through
    ``resolve_cli_findings_cap`` rather than a literal, so bumping that default
    reddens this golden instead of passing silently.
    """
    _, user_prompt = build_git_native_review_prompt(
        chunk=golden_chunks()[0],
        context=golden_review_context(),
        checklist_text=golden_checklist_text(),
        checklist_count=_CHECKLIST_COUNT,
        interaction_paths=_production_interaction_paths(),
        lint_results=None,
        strictness_section=_production_strictness_section(),
        max_findings=resolve_cli_findings_cap(
            transport_is_cli=True,
            cli_max_findings_per_call=AIConfig().cli_max_findings_per_call,
        ),
    )

    assert_golden(
        name="prompt_git_native_chunk_1_production_cli.golden",
        actual=user_prompt,
    )
