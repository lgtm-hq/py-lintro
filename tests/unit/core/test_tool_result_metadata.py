"""Tests for ``ToolResult.metadata``.

``metadata`` is a plain dataclass field again after the deprecated
``ai_metadata`` alias was removed (issue #1831), so these tests pin the
things the removed ``__init__`` wrapper used to touch: keyword construction,
:func:`dataclasses.replace`, and ``__post_init__`` validation.

They also pin the fields the run-level verify pass (#1743) added: ``no_files``
and ``capability``.
"""

from __future__ import annotations

import dataclasses
import warnings

from assertpy import assert_that

from lintro.enums.capability import Cap
from lintro.models.core.tool_result import ToolResult
from lintro.tools.core.verify_pass import (
    VerifyOutcome,
    VerifyScope,
    fold_verify_results,
)


def test_metadata_field_is_the_only_metadata_dataclass_field() -> None:
    """``metadata`` is the sole metadata field on the dataclass."""
    field_names = [f.name for f in dataclasses.fields(ToolResult)]

    assert_that(field_names).contains("metadata")
    assert_that(field_names).does_not_contain("ai_metadata")


def test_construction_with_metadata_keyword() -> None:
    """The keyword populates the field without warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        result = ToolResult(name="ruff", metadata={"fixed_count": 2})

    assert_that(result.metadata).is_equal_to({"fixed_count": 2})


def test_removed_alias_keyword_is_a_type_error() -> None:
    """The removed ``ai_metadata`` keyword is rejected outright."""
    assert_that(ToolResult).raises(TypeError).when_called_with(
        name="ruff",
        ai_metadata={"fixed_count": 2},
    )


def test_removed_alias_attribute_does_not_exist() -> None:
    """Instances expose ``metadata`` only, with no alias attribute."""
    result = ToolResult(name="ruff", metadata={"fixed_count": 1})

    assert_that(hasattr(result, "ai_metadata")).is_false()


def test_dataclasses_replace_preserves_metadata() -> None:
    """``replace`` carries ``metadata`` over and accepts overrides."""
    original = ToolResult(name="ruff", metadata={"fixed_count": 1})

    carried = dataclasses.replace(original, name="black")
    assert_that(carried.name).is_equal_to("black")
    assert_that(carried.metadata).is_equal_to({"fixed_count": 1})

    overridden = dataclasses.replace(original, metadata={"fixed_count": 5})
    assert_that(overridden.metadata).is_equal_to({"fixed_count": 5})


def test_post_init_still_validates_skip_state() -> None:
    """``__post_init__`` validation survives the alias removal."""
    assert_that(ToolResult).raises(ValueError).when_called_with(
        name="ruff",
        skipped=True,
    )
    assert_that(ToolResult).raises(ValueError).when_called_with(
        name="ruff",
        skip_reason="not installed",
    )

    skipped = ToolResult(name="ruff", skipped=True, skip_reason="not installed")
    assert_that(skipped.success).is_true()


def test_post_init_still_validates_issue_counts() -> None:
    """Inconsistent fix counts still raise."""
    assert_that(ToolResult).raises(ValueError).when_called_with(
        name="ruff",
        initial_issues_count=5,
        fixed_issues_count=1,
        remaining_issues_count=1,
        metadata={"fixed_count": 1},
    )


def test_no_files_defaults_to_false_and_is_settable() -> None:
    """``no_files`` marks "nothing was examined", and defaults to off.

    The run-level verify pass (#1743) reads this flag to tell a clean verdict
    apart from a successful non-event, so a real result must not carry it.
    """
    real = ToolResult(name="ruff", success=True, issues_count=0)
    assert_that(real.no_files).is_false()

    nothing_examined = ToolResult(
        name="ruff",
        success=True,
        output="No .py files found to check.",
        issues_count=0,
        no_files=True,
    )
    assert_that(nothing_examined.no_files).is_true()
    # It is a successful non-event, not a skip: the tool stays in reporting.
    assert_that(nothing_examined.success).is_true()
    assert_that(nothing_examined.skipped).is_false()


def test_capability_survives_the_verify_fold() -> None:
    """The folded row keeps the mutation's capability, not the CHECK's.

    Display rolls up to the tool, so the row stays the mutation's; the field
    names what produced it.
    """
    mutation = ToolResult(
        name="ruff",
        success=True,
        output="Fixed 1 issue(s)",
        issues_count=0,
        issues=[],
        initial_issues_count=1,
        fixed_issues_count=1,
        remaining_issues_count=0,
        capability=Cap.FIX,
    )
    verify = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
        capability=Cap.CHECK,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verify)],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    assert_that(results[0].capability).is_equal_to(Cap.FIX)


def test_capability_defaults_to_none() -> None:
    """A result not produced by a capability names none."""
    assert_that(ToolResult(name="ruff").capability).is_none()
