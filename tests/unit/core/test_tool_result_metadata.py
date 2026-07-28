"""Tests for ``ToolResult.metadata`` and its deprecated ``ai_metadata`` alias.

The alias is a property plus an ``__init__`` wrapper rather than a second
dataclass field, so these tests pin the three things that mechanism could
plausibly break: keyword construction, :func:`dataclasses.replace`, and
``__post_init__`` validation.
"""

from __future__ import annotations

import dataclasses
import warnings

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult


def test_metadata_field_is_the_only_metadata_dataclass_field() -> None:
    """The alias must not introduce a second dataclass field."""
    field_names = [f.name for f in dataclasses.fields(ToolResult)]

    assert_that(field_names).contains("metadata")
    assert_that(field_names).does_not_contain("ai_metadata")


def test_construction_with_metadata_keyword() -> None:
    """The new keyword populates the field without warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        result = ToolResult(name="ruff", metadata={"fixed_count": 2})

    assert_that(result.metadata).is_equal_to({"fixed_count": 2})


def test_construction_with_deprecated_keyword_warns_and_populates() -> None:
    """The deprecated keyword still constructs and warns."""
    with pytest.warns(DeprecationWarning, match="ai_metadata is deprecated"):
        result = ToolResult(name="ruff", ai_metadata={"fixed_count": 2})  # type: ignore[call-arg]

    assert_that(result.metadata).is_equal_to({"fixed_count": 2})


def test_both_keywords_together_is_a_type_error() -> None:
    """Supplying both names is rejected rather than silently picking one."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert_that(ToolResult).raises(TypeError).when_called_with(
            name="ruff",
            metadata={"a": 1},
            ai_metadata={"b": 2},
        )


def test_alias_read_and_write_share_one_dict() -> None:
    """Both names are views onto the same underlying dict."""
    result = ToolResult(name="ruff", metadata={"fixed_count": 1})

    with pytest.warns(DeprecationWarning):
        via_alias = result.ai_metadata

    assert_that(via_alias).is_same_as(result.metadata)

    with pytest.warns(DeprecationWarning):
        result.ai_metadata = {"fixed_count": 9}

    assert_that(result.metadata).is_equal_to({"fixed_count": 9})


def test_dataclasses_replace_preserves_metadata() -> None:
    """``replace`` carries ``metadata`` over and accepts overrides."""
    original = ToolResult(name="ruff", metadata={"fixed_count": 1})

    carried = dataclasses.replace(original, name="black")
    assert_that(carried.name).is_equal_to("black")
    assert_that(carried.metadata).is_equal_to({"fixed_count": 1})

    overridden = dataclasses.replace(original, metadata={"fixed_count": 5})
    assert_that(overridden.metadata).is_equal_to({"fixed_count": 5})


def test_post_init_still_validates_skip_state() -> None:
    """``__post_init__`` validation is unaffected by the alias wrapper."""
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
    """Inconsistent fix counts still raise, alias present or not."""
    assert_that(ToolResult).raises(ValueError).when_called_with(
        name="ruff",
        initial_issues_count=5,
        fixed_issues_count=1,
        remaining_issues_count=1,
        metadata={"fixed_count": 1},
    )
