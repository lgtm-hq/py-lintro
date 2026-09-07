"""One body-assembly pipeline for every GitHub comment surface (issue #2304).

Acceptance criterion 2 of #2304 is that a single ``assemble`` is the only thing
that turns sections into a posted comment body. Three renderers used to join
their own lists of strings with their own separator and their own size cap, so
"the sticky and the review body agree" was a property of three implementations
staying in step rather than of one implementation existing.

The criterion is held two ways here: every surface is spied on to prove it
routes through :func:`lintro.ai.review.github_render.assemble`, and the surface
modules are scanned to prove none of them still joins sections itself.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review import github, github_errors, github_render, github_review_body
from lintro.ai.review.github_contract import (
    DEFAULT_BUDGET,
    TRUNCATION_NOTICE,
    CommentBudget,
)
from lintro.ai.review.github_render import Section, assemble
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky import assembly, build_sticky_comment
from lintro.ai.review.sticky import history as sticky_history
from tests.unit.ai.review.golden.github_comment_fixtures import (
    GOLDEN_HEAD_SHA,
    golden_prior_state,
    golden_review_result,
)

#: Modules that render a comment a reviewer reads. Every one of them must
#: reach the shared pipeline rather than re-deriving it.
_SURFACE_MODULES: tuple[str, ...] = (
    "github.py",
    "github_errors.py",
    "github_review_body.py",
    "sticky/assembly.py",
    "sticky/body.py",
    "sticky/history.py",
)

_REVIEW_PACKAGE = Path(__file__).resolve().parents[4] / "lintro" / "ai" / "review"


def _sticky_body() -> str:
    """Render the sticky board from the pinned golden fixtures.

    Returns:
        str: The primary sticky body.
    """
    return build_sticky_comment(
        request=StickyRequest(
            result=golden_review_result(),
            prior_state=golden_prior_state(),
            head_sha=GOLDEN_HEAD_SHA,
        ),
    )


def _review_body() -> str:
    """Render the per-round review body from the pinned golden fixtures.

    Returns:
        str: The review comment body.
    """
    return github_review_body.build_review_body(
        result=golden_review_result(),
        prior_state=ReviewState(),
        match=FindingMatchResult(),
        head_sha=GOLDEN_HEAD_SHA,
    )


def _error_body() -> str:
    """Render the failure comment for a first-round provider outage.

    Returns:
        str: The error comment body.
    """
    return github_errors.format_error_comment(
        error=RuntimeError("provider refused the request"),
        provider="anthropic",
        prior_state=ReviewState(),
    )


def test_every_comment_surface_binds_the_one_assemble() -> None:
    """Each surface holds the same ``assemble`` object, not a copy of it."""
    bound = {
        name: getattr(module, "assemble")  # noqa: B009 - the point is the binding
        for name, module in (
            ("github", github),
            ("github_errors", github_errors),
            ("github_review_body", github_review_body),
            ("sticky.assembly", assembly),
            ("sticky.history", sticky_history),
        )
    }

    assert_that(set(bound.values())).is_length(1)
    assert_that(next(iter(bound.values()))).is_same_as(github_render.assemble)


@pytest.mark.parametrize(
    ("module_name", "render", "expected_section"),
    [
        ("sticky.assembly", _sticky_body, "findings_round"),
        ("github_review_body", _review_body, "header"),
        ("github_errors", _error_body, "guidance"),
    ],
)
def test_each_posting_path_assembles_through_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    render: Callable[[], str],
    expected_section: str,
) -> None:
    """Spying on one surface's ``assemble`` sees that surface's whole body.

    The named section is one the surface cannot render without, so a body that
    reached the pipeline carrying something unrelated fails here rather than
    passing an existence check.

    Args:
        monkeypatch: Fixture used to swap the module's bound ``assemble``.
        module_name: Surface under test, for the failure message.
        render: Callable driving that surface end to end.
        expected_section: Section this surface must always assemble.
    """
    calls: list[tuple[Section, ...]] = []

    def spy(
        *,
        sections: Sequence[Section],
        budget: CommentBudget | None = DEFAULT_BUDGET,
    ) -> str:
        """Record the sections and delegate to the real pipeline.

        Args:
            sections: Sections the surface assembled.
            budget: Budget the surface asked for.

        Returns:
            str: Whatever the real ``assemble`` returns.
        """
        calls.append(tuple(sections))
        return assemble(sections=sections, budget=budget)

    module = {
        "sticky.assembly": assembly,
        "github_review_body": github_review_body,
        "github_errors": github_errors,
    }[module_name]
    monkeypatch.setattr(module, "assemble", spy)

    body = render()

    assert_that(calls).described_as(f"{module_name} bypassed assemble").is_not_empty()
    assert_that(body).is_not_empty()
    assert_that(calls[-1]).extracting("name").contains(expected_section)


def test_no_surface_joins_its_own_sections() -> None:
    r"""No comment renderer still concatenates sections with a blank line.

    The section separator is the pipeline's, so a surviving ``"\\n\\n".join``
    in one of these modules is a second body assembler by another name. Joins
    *inside* one section (``"\\n".join``) are the section's own business and
    are deliberately not matched.
    """
    offenders: dict[str, int] = {}
    for name in _SURFACE_MODULES:
        path = _REVIEW_PACKAGE / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "join":
                continue
            if isinstance(func.value, ast.Constant) and func.value.value == "\n\n":
                offenders[name] = node.lineno

    assert_that(offenders).is_empty()


def test_assemble_drops_empty_sections_and_caps_the_result() -> None:
    """The pipeline omits empty sections and enforces the budget it is given."""
    body = assemble(
        sections=[
            Section(name="first", text="one"),
            Section(name="skipped", text=""),
            Section(name="second", text="two"),
        ],
        budget=None,
    )
    capped = assemble(
        sections=[Section(name="huge", text="x" * 500)],
        budget=CommentBudget(max_chars=200),
    )

    assert_that(body).is_equal_to("one\n\ntwo")
    assert_that(len(capped)).is_less_than_or_equal_to(200)
    assert_that(capped).contains(TRUNCATION_NOTICE.strip())


def test_an_absent_question_map_renders_the_same_board() -> None:
    """``question_map=None`` is normalized to an empty map, not passed through.

    The request documents ``None`` as "no questions", and the plan the section
    renderers read holds a real mapping, so the normalization is a contract a
    caller relies on rather than an implementation detail.
    """

    def board(question_map: dict[int, str] | None) -> str:
        """Render the pinned board with the given question map.

        Args:
            question_map: Prompt id to question text, or ``None``.

        Returns:
            str: The primary sticky body.
        """
        return build_sticky_comment(
            request=StickyRequest(
                result=golden_review_result(),
                prior_state=golden_prior_state(),
                head_sha=GOLDEN_HEAD_SHA,
                question_map=question_map,
            ),
        )

    assert_that(board(None)).is_equal_to(board({}))
