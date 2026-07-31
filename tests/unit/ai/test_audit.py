"""Tests for the AI audit log writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from assertpy import assert_that

from lintro.ai.audit import (
    AUDIT_DIR,
    AUDIT_JSONL_FILE,
    write_audit_log,
)
from lintro.ai.models import AIFixSuggestion


@pytest.fixture
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


def _audit_path(tmp_path: Path) -> Path:
    """Return the JSONL audit path for a workspace root."""
    return tmp_path / AUDIT_DIR / AUDIT_JSONL_FILE


def _read_records(tmp_path: Path) -> list[dict[str, Any]]:
    """Parse each JSONL line into a dict."""
    text = _audit_path(tmp_path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_writes_jsonl_file(tmp_path: Path, suggestion: AIFixSuggestion) -> None:
    """write_audit_log creates a JSONL audit file with one record."""
    write_audit_log(tmp_path, [suggestion], rejected_count=1, total_cost=0.005)

    assert_that(_audit_path(tmp_path).exists()).is_true()

    records = _read_records(tmp_path)
    assert_that(records).is_length(1)
    assert_that(records[0]).contains_key(
        "timestamp",
        "applied_count",
        "rejected_count",
        "total_cost_usd",
        "entries",
    )


def test_each_line_is_valid_json(
    tmp_path: Path,
    suggestion: AIFixSuggestion,
) -> None:
    """Every line of the audit log parses as an independent JSON object."""
    write_audit_log(tmp_path, [suggestion], rejected_count=0, total_cost=0.0)
    write_audit_log(tmp_path, [], rejected_count=1, total_cost=0.0)

    lines = _audit_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert_that(lines).is_length(2)
    for line in lines:
        parsed = json.loads(line)
        assert_that(parsed).is_type_of(dict)


def test_appends_across_runs(tmp_path: Path, suggestion: AIFixSuggestion) -> None:
    """Multiple runs append records instead of overwriting history."""
    write_audit_log(tmp_path, [suggestion], rejected_count=1, total_cost=0.001)
    write_audit_log(tmp_path, [], rejected_count=2, total_cost=0.002)
    write_audit_log(tmp_path, [suggestion], rejected_count=3, total_cost=0.003)

    records = _read_records(tmp_path)
    assert_that(records).is_length(3)
    assert_that([r["rejected_count"] for r in records]).is_equal_to([1, 2, 3])


def test_preserves_existing_content(
    tmp_path: Path,
    suggestion: AIFixSuggestion,
) -> None:
    """A pre-existing audit line is preserved when a new run appends."""
    audit_dir = tmp_path / AUDIT_DIR
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / AUDIT_JSONL_FILE).write_text(
        json.dumps({"marker": "prior-run"}) + "\n",
        encoding="utf-8",
    )

    write_audit_log(tmp_path, [suggestion], rejected_count=1, total_cost=0.001)

    records = _read_records(tmp_path)
    assert_that(records).is_length(2)
    assert_that(records[0]["marker"]).is_equal_to("prior-run")


def test_contains_correct_fields(
    tmp_path: Path,
    suggestion: AIFixSuggestion,
) -> None:
    """Audit entries contain all expected fields from the suggestion."""
    write_audit_log(tmp_path, [suggestion], rejected_count=2, total_cost=0.005)

    record = _read_records(tmp_path)[0]
    assert_that(record["applied_count"]).is_equal_to(1)
    assert_that(record["rejected_count"]).is_equal_to(2)
    assert_that(record["entries"]).is_length(1)

    entries = cast(list[dict[str, Any]], record["entries"])
    entry = entries[0]
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

    record = _read_records(tmp_path)[0]
    assert_that(record["applied_count"]).is_equal_to(0)
    assert_that(record["entries"]).is_empty()


def test_rounds_cost_properly(tmp_path: Path) -> None:
    """Total cost is rounded to 6 decimal places."""
    write_audit_log(tmp_path, [], rejected_count=0, total_cost=0.1234567890)

    record = _read_records(tmp_path)[0]
    assert_that(record["total_cost_usd"]).is_equal_to(0.123457)


def test_rotation_bounds_file_growth(tmp_path: Path) -> None:
    """Records beyond max_entries drop the oldest lines."""
    for i in range(5):
        write_audit_log(
            tmp_path,
            [],
            rejected_count=i,
            total_cost=0.0,
            max_entries=3,
        )

    records = _read_records(tmp_path)
    assert_that(records).is_length(3)
    # Oldest (rejected_count 0 and 1) rotated out; newest retained.
    assert_that([r["rejected_count"] for r in records]).is_equal_to([2, 3, 4])


def test_rotation_disabled_keeps_full_history(tmp_path: Path) -> None:
    """max_entries=None retains every appended record."""
    for i in range(4):
        write_audit_log(
            tmp_path,
            [],
            rejected_count=i,
            total_cost=0.0,
            max_entries=None,
        )

    assert_that(_read_records(tmp_path)).is_length(4)
