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
from collections.abc import Sequence
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.capability import Cap
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools.core.verify_pass import (
    VerifyOutcome,
    VerifyScope,
    fold_verify_results,
)


@pytest.mark.parametrize("field_name", ["issues", "initial_issues", "metadata"])
def test_collection_defaults_are_empty_containers(field_name: str) -> None:
    """Default results expose usable collections without normalization.

    Args:
        field_name: Collection whose default is part of the result contract.
    """
    result = ToolResult()
    expected: object = {} if field_name == "metadata" else ()

    assert_that(getattr(result, field_name)).is_equal_to(expected)


@pytest.mark.parametrize("field_name", ["issues", "initial_issues", "metadata"])
@pytest.mark.parametrize("skipped", [False, True])
def test_none_collections_are_rejected_at_construction(
    field_name: str,
    skipped: bool,
) -> None:
    """Unchecked plugin payloads fail at the model boundary, even for skips.

    Args:
        field_name: Collection incorrectly supplied as None by a plugin.
        skipped: Whether the plugin reports a skip instead of an execution.
    """
    payload: dict[str, Any] = {
        field_name: None,
        "skipped": skipped,
        "skip_reason": "tool unavailable" if skipped else None,
    }

    with pytest.raises(TypeError, match=rf"ToolResult\.{field_name} must not be None"):
        ToolResult(**payload)


@pytest.mark.parametrize("empty", [(), []], ids=["tuple", "list"])
def test_explicit_empty_collections_preserve_unknown_counts(
    empty: Sequence[BaseIssue],
) -> None:
    """Empty plugin collections do not manufacture measured fix counts.

    Args:
        empty: A supported empty sequence supplied explicitly by a plugin.
    """
    result = ToolResult(issues=empty, initial_issues=empty, metadata={})

    assert_that(result.issues).is_equal_to(empty)
    assert_that(result.initial_issues).is_equal_to(empty)
    assert_that(result.metadata).is_empty()
    assert_that(result.initial_issues_count).is_none()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.success).is_false()


def test_default_metadata_is_owned_by_each_result() -> None:
    """Attaching metadata to one default result cannot mutate another."""
    first = ToolResult()
    second = ToolResult()

    first.metadata["fixed_count"] = 2

    assert_that(first.metadata).is_equal_to({"fixed_count": 2})
    assert_that(second.metadata).is_empty()


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


@pytest.mark.parametrize("residual_unknown", [False, True])
def test_api_v2_fully_positional_constructor_preserves_fields(
    residual_unknown: bool,
) -> None:
    """Every positional argument from API v2 keeps its original meaning.

    Args:
        residual_unknown: Whether fix accounting was left unmeasured.
    """
    issue = BaseIssue(file="a.py", line=1, message="finding")
    reason = "verification incomplete" if residual_unknown else None
    result = ToolResult(
        "plugin",
        False,
        "native output",
        1,
        "formatted output",
        [issue],
        2,
        None,
        None,
        (issue,),
        {"passed": 2},
        {"fixed_count": 1},
        "/repo",
        False,
        False,
        None,
        False,
        3,
        1.25,
        residual_unknown,
        reason,
        Cap.FIX,
        True,
    )

    assert_that(result.name).is_equal_to("plugin")
    assert_that(result.issues).is_equal_to([issue])
    assert_that(result.metadata).is_equal_to({"fixed_count": 1})
    assert_that(result.parse_failures_count).is_equal_to(3)
    assert_that(result.duration_seconds).is_equal_to(1.25)
    assert_that(result.residual_unknown).is_equal_to(residual_unknown)
    assert_that(result.residual_unknown_reason).is_equal_to(reason)
    assert_that(result.capability).is_equal_to(Cap.FIX)
    assert_that(result.inspected_files).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.findings_complete).is_false()


def test_api_v2_match_args_prefix_is_preserved() -> None:
    """Positional patterns retain the field order published on origin/main."""
    legacy_fields = (
        "name",
        "success",
        "output",
        "issues_count",
        "formatted_output",
        "issues",
        "initial_issues_count",
        "fixed_issues_count",
        "remaining_issues_count",
        "initial_issues",
        "pytest_summary",
        "metadata",
        "cwd",
        "no_files",
        "skipped",
        "skip_reason",
        "timed_out",
        "parse_failures_count",
        "duration_seconds",
        "residual_unknown",
        "residual_unknown_reason",
        "capability",
        "inspected_files",
    )

    assert_that(ToolResult.__match_args__[: len(legacy_fields)]).is_equal_to(
        legacy_fields,
    )


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

    skipped = ToolResult(
        name="ruff",
        skipped=True,
        skip_reason="not installed",
        inspected_files=False,
        success=True,
    )
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


def test_no_files_defaults_to_false_and_is_explicit() -> None:
    """``no_files`` marks "nothing was examined", and defaults to off.

    Two consumers read it: ``lintro.tools.core.verify_pass.run_verify_pass``,
    which must tell a clean verdict apart from a successful non-event, and
    ``lintro.utils.meaningful_run.result_inspected_files``, which gates the
    badge and the severity baseline on whether anything was measured at all.
    A real result must not carry it.
    """
    real = ToolResult(name="ruff", success=True, issues_count=0)
    assert_that(real.no_files).is_false()
    assert_that(real.inspected_files).is_none()

    nothing_examined = ToolResult(
        name="ruff",
        success=True,
        output="No .py files found to check.",
        issues_count=0,
        no_files=True,
        inspected_files=False,
    )
    assert_that(nothing_examined.no_files).is_true()
    assert_that(nothing_examined.inspected_files).is_false()
    # It is a successful non-event, not a skip: the tool stays in reporting.
    assert_that(nothing_examined.success).is_true()
    assert_that(nothing_examined.skipped).is_false()


@pytest.mark.parametrize("inspected", [None, False, True])
def test_inspection_signal_survives_result_copy(inspected: bool | None) -> None:
    """Legacy unknown, decline and completed inspection remain distinct.

    Args:
        inspected: Producer's inspection signal.
    """
    result = ToolResult(name="plugin", success=True, inspected_files=inspected)
    copied = dataclasses.replace(result, output="Changed display text")

    assert_that(copied.inspected_files).is_equal_to(inspected)
    assert_that(copied.no_files).is_false()


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


@pytest.mark.parametrize(
    ("fixed", "remaining"),
    [
        (None, 0),
        (0, None),
        (2, None),
        (2, 0),
    ],
)
def test_an_unknown_residual_may_not_carry_a_measured_count(
    fixed: int | None,
    remaining: int | None,
) -> None:
    """The model refuses "unknown" beside a number, zero included.

    The falsy counts are in the matrix on purpose: a validator that regressed
    from ``is not None`` to truthiness would accept ``fixed_issues_count=0``
    and ``remaining_issues_count=0`` beside the flag, which is exactly the
    "unknown, and also zero" rendering this rejects.

    ``residual_unknown`` means the verify pass took no after-measurement
    (#1743), so a result that still carries ``fixed_issues_count`` or
    ``remaining_issues_count`` would render "unknown" in one column and a
    count nobody measured in the next. A producer that folds incorrectly has
    to fail here, where the contradiction is, rather than at the display.

    Args:
        fixed: Net resolved count the producer wrongly kept, or ``None``.
        remaining: Residual count the producer wrongly kept, or ``None``.
    """
    with pytest.raises(ValueError, match="must be None when residual_unknown"):
        ToolResult(
            name="ruff",
            success=False,
            issues_count=2,
            fixed_issues_count=fixed,
            remaining_issues_count=remaining,
            residual_unknown=True,
            residual_unknown_reason="the verify check timed out",
        )


def test_an_unknown_residual_may_not_report_success() -> None:
    """The other half of the contract: an unmeasured residual fails the run.

    Condition 4 of this change is that verification failure fails the run and
    is never presented as a measured after-count. The counts half was already
    enforced; a result that admits it measured nothing while claiming
    ``success=True`` would exit 0 on an outcome nobody knows, so the model
    refuses it too.
    """
    with pytest.raises(ValueError, match="success must be False"):
        ToolResult(
            name="ruff",
            success=True,
            issues_count=2,
            residual_unknown=True,
            residual_unknown_reason="the verify check timed out",
        )


def test_an_unknown_residual_with_both_counts_cleared_is_valid() -> None:
    """The shape the fold produces constructs without complaint.

    ``issues_count`` stays populated in this state: it is the before-count,
    the pre-fix findings carried forward, not a residual.
    """
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=2,
        initial_issues_count=2,
        residual_unknown=True,
        residual_unknown_reason="the verify check timed out",
    )

    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues_count).is_equal_to(2)
