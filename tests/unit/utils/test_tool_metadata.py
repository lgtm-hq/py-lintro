"""Tests for the core tool-metadata helpers.

``normalize_tool_metadata`` moved out of ``lintro.ai.metadata`` in issue #724
because it is a pure dict whitelist with no AI dependency — it even passes
osv-scanner's non-AI ``suppressions`` through.
"""

from __future__ import annotations

from assertpy import assert_that

import lintro.ai.metadata as ai_metadata_module
from lintro.models.core.tool_result import ToolResult
from lintro.utils.tool_metadata import get_ai_count, normalize_tool_metadata


def test_ai_package_no_longer_re_exports_the_core_function() -> None:
    """The ``lintro.ai.metadata`` compatibility shim is gone (issue #1831)."""
    assert_that(hasattr(ai_metadata_module, "normalize_ai_metadata")).is_false()


def test_normalize_drops_unknown_keys() -> None:
    """Only whitelisted keys survive normalization."""
    normalized = normalize_tool_metadata(
        {"fixed_count": 3, "not_a_real_key": "x"},
    )

    assert_that(normalized).contains_key("fixed_count")
    assert_that(normalized).does_not_contain_key("not_a_real_key")


def test_normalize_backfills_applied_count_from_fixed_count() -> None:
    """Legacy metadata without ``applied_count`` is upgraded."""
    normalized = normalize_tool_metadata({"fixed_count": 4})

    assert_that(normalized["applied_count"]).is_equal_to(4)


def test_normalize_accepts_legacy_suggestions_key() -> None:
    """``suggestions`` is normalized to ``fix_suggestions``."""
    normalized = normalize_tool_metadata({"suggestions": [{"file": "a.py"}]})

    assert_that(normalized["fix_suggestions"]).is_equal_to([{"file": "a.py"}])


def test_normalize_passes_through_osv_suppressions() -> None:
    """The non-AI osv-scanner branch keeps working."""
    normalized = normalize_tool_metadata(
        {"suppressions": [{"id": "GHSA-x", "status": "stale"}, "junk"]},
    )

    assert_that(normalized["suppressions"]).is_equal_to(
        [{"id": "GHSA-x", "status": "stale"}],
    )


def test_normalize_deep_copies_ai_metrics() -> None:
    """``ai_metrics`` is copied so callers cannot mutate the source."""
    raw = {"ai_metrics": {"tokens": {"input": 1}}}

    normalized = normalize_tool_metadata(raw)
    normalized["ai_metrics"]["tokens"]["input"] = 99

    assert_that(raw["ai_metrics"]["tokens"]["input"]).is_equal_to(1)


def test_get_ai_count_reads_the_renamed_field() -> None:
    """The counter reads ``metadata``, not the deprecated alias."""
    result = ToolResult(name="ruff", metadata={"applied_count": 7})

    assert_that(get_ai_count(result, "applied_count")).is_equal_to(7)


def test_get_ai_count_falls_back_to_fixed_count() -> None:
    """Legacy metadata without ``applied_count`` still counts."""
    result = ToolResult(name="ruff", metadata={"fixed_count": 2})

    assert_that(get_ai_count(result, "applied_count")).is_equal_to(2)


def test_get_ai_count_without_metadata_is_zero() -> None:
    """A result with no metadata yields zero."""
    assert_that(get_ai_count(ToolResult(name="ruff"), "applied_count")).is_zero()
