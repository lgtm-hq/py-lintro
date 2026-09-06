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

from lintro.ai.review import (
    github_errors,
    github_inline,
    github_render,
    github_review_body,
)
from lintro.ai.review.github_contract import (
    DEFAULT_BUDGET,
    TRUNCATION_NOTICE,
    CommentBudget,
)
from lintro.ai.review.github_render import Section, assemble
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky import assembly, build_sticky_comment
from lintro.ai.review.sticky import history as sticky_history
from tests.unit.ai.review.golden.github_comment_fixtures import (
    GOLDEN_HEAD_SHA,
    golden_match,
    golden_prior_state,
    golden_review_result,
)

#: Modules that render a comment a reviewer reads. Every one of them must
#: reach the shared pipeline rather than re-deriving it.
_SURFACE_MODULES: tuple[str, ...] = (
    "github_inline.py",
    "github_errors.py",
    "github_review_body.py",
    "sticky/assembly.py",
    "sticky/body.py",
    "sticky/history.py",
)

_REVIEW_PACKAGE = Path(__file__).resolve().parents[4] / "lintro" / "ai" / "review"


def _sticky_body() -> str:
    """Render the sticky board from the pinned golden fixtures.

    ``transport`` and ``auth_mode`` come from the fixture metadata rather than
    being left at their empty defaults: the posting path always stamps them,
    and an empty ``auth_mode`` takes the sticky down a degraded cost-basis
    branch the posted comment never reaches.

    Returns:
        str: The primary sticky body.
    """
    result = golden_review_result()
    return build_sticky_comment(
        request=StickyRequest(
            result=result,
            prior_state=golden_prior_state(),
            head_sha=GOLDEN_HEAD_SHA,
            transport=result.metadata.transport,
            auth_mode=result.metadata.auth_mode,
            cost_basis=result.metadata.cost_basis,
        ),
    )


def _review_body() -> str:
    """Render the per-round review body from the pinned golden fixtures.

    The match is the production matcher's output over the pinned prior state,
    not an empty ``FindingMatchResult()``: an empty match renders a board
    with no rows, which is the one shape that cannot show the pipeline
    carrying real content.

    Returns:
        str: The review comment body.
    """
    result = golden_review_result()
    return github_review_body.build_review_body(
        result=result,
        prior_state=golden_prior_state(),
        match=golden_match(),
        head_sha=GOLDEN_HEAD_SHA,
        transport=result.metadata.transport,
        auth_mode=result.metadata.auth_mode,
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
            ("github_inline", github_inline),
            ("github_errors", github_errors),
            ("github_review_body", github_review_body),
            ("sticky.assembly", assembly),
            ("sticky.history", sticky_history),
        )
    }

    assert_that(set(bound.values())).is_length(1)
    assert_that(next(iter(bound.values()))).is_same_as(github_render.assemble)


@pytest.mark.parametrize(
    ("module_name", "render", "expected_section", "expected_budget"),
    [
        ("sticky.assembly", _sticky_body, "findings_round", None),
        ("github_review_body", _review_body, "header", DEFAULT_BUDGET),
        ("github_errors", _error_body, "guidance", DEFAULT_BUDGET),
    ],
)
def test_each_posting_path_assembles_through_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    render: Callable[[], str],
    expected_section: str,
    expected_budget: CommentBudget | None,
) -> None:
    """Spying on one surface's ``assemble`` sees that surface's whole body.

    The named section is one the surface cannot render without, so a body that
    reached the pipeline carrying something unrelated fails here rather than
    passing an existence check.

    The budget is asserted alongside the sections because it decides whether
    the surface caps its own body: the sticky passes ``None`` and lets
    :func:`~lintro.ai.review.github_contract.fit_body` own the size contract,
    while the two single-shot surfaces cap at ``DEFAULT_BUDGET`` in the call
    itself. A surface that started passing its own budget would still route
    through the pipeline and still assemble the right sections.

    The assertion picks the call carrying the expected section rather than the
    last one: a surface driven through ``fit_body`` assembles once per pruning
    probe, and ``largest_fitting`` returns the largest body that *fit* rather
    than the last one it rendered (``github_contract.py`` lines 240-247), so
    the final call is not necessarily the posted body.

    Args:
        monkeypatch: Fixture used to swap the module's bound ``assemble``.
        module_name: Surface under test, for the failure message.
        render: Callable driving that surface end to end.
        expected_section: Section this surface must always assemble.
        expected_budget: Budget this surface asks the pipeline for.
    """
    calls: list[tuple[tuple[Section, ...], CommentBudget | None]] = []

    def spy(
        *,
        sections: Sequence[Section],
        budget: CommentBudget | None = DEFAULT_BUDGET,
    ) -> str:
        """Record the sections and the budget, then delegate to the pipeline.

        Args:
            sections: Sections the surface assembled.
            budget: Budget the surface asked for.

        Returns:
            str: Whatever the real ``assemble`` returns.
        """
        calls.append((tuple(sections), budget))
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
    carrying = [
        (sections, budget)
        for sections, budget in calls
        if expected_section in {section.name for section in sections}
    ]
    assert_that(carrying).described_as(
        f"{module_name} never assembled {expected_section}",
    ).is_not_empty()
    assert_that([budget for _sections, budget in carrying]).is_equal_to(
        [expected_budget] * len(carrying),
    )


def _blank_line_constant_names(*, tree: ast.Module) -> frozenset[str]:
    r"""Return the names a module binds to the blank-line separator.

    Args:
        tree: Parsed module to scan.

    Returns:
        frozenset[str]: Names assigned the literal ``"\\n\\n"``.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or value.value != "\n\n":
            continue
        names.update(
            target.id for target in node.targets if isinstance(target, ast.Name)
        )
    return frozenset(names)


def test_no_surface_joins_its_own_sections() -> None:
    r"""No comment renderer still concatenates sections with a blank line.

    The section separator is the pipeline's, so a surviving ``"\\n\\n".join``
    in one of these modules is a second body assembler by another name. Joins
    *inside* one section (``"\\n".join``) are the section's own business and
    are deliberately not matched.

    A module-level constant holding the separator is matched too: binding
    ``"\\n\\n"`` to a name and joining on that is the same assembler wearing a
    different spelling, and a scan that only looked at string literals would
    wave it through.
    """
    offenders: dict[str, int] = {}
    for name in _SURFACE_MODULES:
        path = _REVIEW_PACKAGE / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        separator_names = _blank_line_constant_names(tree=tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "join":
                continue
            target = func.value
            joins_blank_line = (
                isinstance(target, ast.Constant) and target.value == "\n\n"
            ) or (isinstance(target, ast.Name) and target.id in separator_names)
            if joins_blank_line:
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
