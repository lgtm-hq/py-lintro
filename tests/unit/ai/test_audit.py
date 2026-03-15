"""Tests for the AI audit log writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.audit import AUDIT_DIR, AUDIT_FILE, write_audit_log
from lintro.ai.models import AIFixSuggestion


@pytest.fixture()
def suggestion() -> AIFixSuggestion:
    """A sample AIFixSuggestion for testing."""
    return AIFixSuggestion(
        file="src/app.py",
        line=42,
        code="E501",
        tool_name="ruff",
        original_code="x = 1",
        suggested_code="x = 2",
        diff="--- a\n+++ b\n",
        explanation="shortened line",
        confidence="high",
        risk_level="safe-style",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.001,
    )


def test_writes_json_file(tmp_path: Path, suggestion: AIFixSuggestion) -> None:
    """write_audit_log creates a JSON audit file."""
    write_audit_log(tmp_path, [suggestion], rejected_count=1, total_cost=0.005)

    audit_file = tmp_path / AUDIT_DIR / AUDIT_FILE
    assert_that(audit_file.exists()).is_true()

    data = json.loads(audit_file.read_text())
    assert_that(data).contains_key(
        "timestamp",
        "applied_count",
        "rejected_count",
        "total_cost_usd",
        "entries",
    )


def test_contains_correct_fields(tmp_path: Path, suggestion: AIFixSuggestion) -> None:
    """Audit entries contain all expected fields from the suggestion."""
    write_audit_log(tmp_path, [suggestion], rejected_count=2, total_cost=0.005)

    data = json.loads((tmp_path / AUDIT_DIR / AUDIT_FILE).read_text())
    assert_that(data["applied_count"]).is_equal_to(1)
    assert_that(data["rejected_count"]).is_equal_to(2)
    assert_that(data["entries"]).is_length(1)

    entry = data["entries"][0]
    assert_that(entry["file"]).is_equal_to("src/app.py")
    assert_that(entry["line"]).is_equal_to(42)
    assert_that(entry["code"]).is_equal_to("E501")
    assert_that(entry["tool"]).is_equal_to("ruff")
    assert_that(entry["action"]).is_equal_to("applied")
    assert_that(entry["confidence"]).is_equal_to("high")
    assert_that(entry["risk_level"]).is_equal_to("safe-style")


def test_handles_empty_applied_list(tmp_path: Path) -> None:
    """An empty applied list produces zero entries."""
    write_audit_log(tmp_path, [], rejected_count=5, total_cost=0.0)

    data = json.loads((tmp_path / AUDIT_DIR / AUDIT_FILE).read_text())
    assert_that(data["applied_count"]).is_equal_to(0)
    assert_that(data["entries"]).is_empty()


def test_rounds_cost_properly(tmp_path: Path) -> None:
    """Total cost is rounded to 6 decimal places."""
    write_audit_log(tmp_path, [], rejected_count=0, total_cost=0.1234567890)

    data = json.loads((tmp_path / AUDIT_DIR / AUDIT_FILE).read_text())
    assert_that(data["total_cost_usd"]).is_equal_to(0.123457)
