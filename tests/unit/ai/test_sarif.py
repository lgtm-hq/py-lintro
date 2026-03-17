"""Tests for SARIF output format (#706)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.ai.output.sarif import (
    SARIF_SCHEMA,
    SARIF_VERSION,
    _confidence_to_score,
    _risk_to_sarif_level,
    render_fixes_sarif,
    to_sarif,
    write_sarif,
)

# -- TestRiskToSarifLevel: Tests for risk level to SARIF level mapping. ------


@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("high", "error"),
        ("critical", "error"),
        ("medium", "warning"),
        ("behavioral-risk", "warning"),
        ("low", "note"),
        ("safe-style", "note"),
        ("", "warning"),
        ("HIGH", "error"),
    ],
)
def test_risk_to_sarif_level(input_val: str, expected: str) -> None:
    """Map risk level to SARIF severity level."""
    assert_that(_risk_to_sarif_level(input_val)).is_equal_to(expected)


# -- TestConfidenceToScore: Tests for confidence label to score mapping. -----


@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("high", 0.9),
        ("medium", 0.6),
        ("low", 0.3),
        ("unknown", 0.5),
        ("", 0.5),
    ],
)
def test_confidence_to_score(input_val: str, expected: float) -> None:
    """Map confidence label to numeric score."""
    assert_that(_confidence_to_score(input_val)).is_equal_to(expected)


# -- TestToSarif: Tests for SARIF document generation. -----------------------


def test_empty_suggestions_produces_valid_sarif() -> None:
    """Produce valid SARIF structure with no suggestions."""
    sarif = to_sarif([])
    assert_that(sarif["$schema"]).is_equal_to(SARIF_SCHEMA)
    assert_that(sarif["version"]).is_equal_to(SARIF_VERSION)
    assert_that(sarif["runs"]).is_length(1)
    assert_that(sarif["runs"][0]["results"]).is_empty()


def test_single_suggestion_produces_result() -> None:
    """Convert a single suggestion to a SARIF result."""
    s = AIFixSuggestion(
        file="src/main.py",
        line=10,
        code="B101",
        tool_name="bandit",
        explanation="Replace assert with if/raise",
        confidence="high",
        risk_level="low",
        suggested_code="if not x:\n    raise ValueError",
        cost_estimate=0.002,
    )
    sarif = to_sarif([s])

    run = sarif["runs"][0]
    assert_that(run["tool"]["driver"]["name"]).is_equal_to("lintro-ai")

    rules = run["tool"]["driver"]["rules"]
    assert_that(rules).is_length(1)
    assert_that(rules[0]["id"]).is_equal_to("bandit/B101")
    assert_that(rules[0]["fullDescription"]["text"]).is_equal_to(
        "Replace assert with if/raise",
    )

    results = run["results"]
    assert_that(results).is_length(1)
    result = results[0]
    assert_that(result["ruleId"]).is_equal_to("bandit/B101")
    assert_that(result["level"]).is_equal_to("note")
    assert_that(result["message"]["text"]).is_equal_to(
        "Replace assert with if/raise",
    )

    location = result["locations"][0]["physicalLocation"]
    assert_that(location["artifactLocation"]["uri"]).is_equal_to("src/main.py")
    assert_that(location["region"]["startLine"]).is_equal_to(10)

    assert_that(result["fixes"]).is_length(1)
    assert_that(result["properties"]["confidence"]).is_equal_to("high")
    assert_that(result["properties"]["confidenceScore"]).is_equal_to(0.9)


def test_multiple_suggestions_same_rule_deduplicates_rules() -> None:
    """Deduplicate rules when multiple suggestions share the same code."""
    suggestions = [
        AIFixSuggestion(
            file="a.py",
            line=1,
            code="B101",
            explanation="Fix 1",
            risk_level="low",
        ),
        AIFixSuggestion(
            file="b.py",
            line=2,
            code="B101",
            explanation="Fix 2",
            risk_level="low",
        ),
    ]
    sarif = to_sarif(suggestions)
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert_that(rules).is_length(1)
    results = sarif["runs"][0]["results"]
    assert_that(results).is_length(2)


def test_different_rules_create_separate_entries() -> None:
    """Create separate rule entries for different codes."""
    suggestions = [
        AIFixSuggestion(code="B101", explanation="Fix 1", risk_level="low"),
        AIFixSuggestion(code="E501", explanation="Fix 2", risk_level="medium"),
    ]
    sarif = to_sarif(suggestions)
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert_that(rules).is_length(2)


def test_risk_level_maps_correctly() -> None:
    """Map risk levels to correct SARIF severity levels."""
    suggestions = [
        AIFixSuggestion(code="A", risk_level="high", explanation="x"),
        AIFixSuggestion(code="B", risk_level="medium", explanation="y"),
        AIFixSuggestion(code="C", risk_level="low", explanation="z"),
    ]
    sarif = to_sarif(suggestions)
    levels = [r["level"] for r in sarif["runs"][0]["results"]]
    assert_that(levels).is_equal_to(["error", "warning", "note"])


def test_summary_attached_as_run_properties() -> None:
    """Attach summary as SARIF run properties."""
    summary = AISummary(
        overview="High-level assessment",
        key_patterns=["Missing types"],
        priority_actions=["Add type hints"],
        estimated_effort="2 hours",
    )
    sarif = to_sarif([], summary=summary)
    props = sarif["runs"][0]["properties"]["aiSummary"]
    assert_that(props["overview"]).is_equal_to("High-level assessment")
    assert_that(props["keyPatterns"]).contains("Missing types")
    assert_that(props["priorityActions"]).contains("Add type hints")
    assert_that(props["estimatedEffort"]).is_equal_to("2 hours")


def test_no_summary_omits_run_properties() -> None:
    """Omit run properties when no summary is provided."""
    sarif = to_sarif([])
    assert_that(sarif["runs"][0]).does_not_contain_key("properties")


def test_custom_tool_name_and_version() -> None:
    """Use custom tool name and version in driver metadata."""
    sarif = to_sarif([], tool_name="custom-tool", tool_version="1.2.3")
    driver = sarif["runs"][0]["tool"]["driver"]
    assert_that(driver["name"]).is_equal_to("custom-tool")
    assert_that(driver["version"]).is_equal_to("1.2.3")


def test_no_file_omits_locations() -> None:
    """Omit locations when no file is specified."""
    s = AIFixSuggestion(code="X", explanation="No file", risk_level="low")
    sarif = to_sarif([s])
    result = sarif["runs"][0]["results"][0]
    assert_that(result).does_not_contain_key("locations")


def test_no_suggested_code_omits_fixes() -> None:
    """Omit fixes when no suggested code is provided."""
    s = AIFixSuggestion(
        file="x.py",
        line=1,
        code="X",
        explanation="No fix",
        risk_level="low",
    )
    sarif = to_sarif([s])
    result = sarif["runs"][0]["results"][0]
    assert_that(result).does_not_contain_key("fixes")


def test_tool_name_in_rule_properties() -> None:
    """Include tool name in rule properties."""
    s = AIFixSuggestion(
        code="B101",
        tool_name="bandit",
        explanation="Fix",
        risk_level="low",
    )
    sarif = to_sarif([s])
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    assert_that(rule["properties"]["tool"]).is_equal_to("bandit")


# -- TestRenderFixesSarif: Tests for SARIF JSON string rendering. ------------


def test_returns_valid_json() -> None:
    """Return valid JSON string from suggestions."""
    s = AIFixSuggestion(code="B101", explanation="Fix", risk_level="low")
    result = render_fixes_sarif([s])
    parsed = json.loads(result)
    assert_that(parsed["version"]).is_equal_to(SARIF_VERSION)


def test_empty_suggestions_returns_valid_json() -> None:
    """Return valid JSON string with empty suggestions."""
    result = render_fixes_sarif([])
    parsed = json.loads(result)
    assert_that(parsed["runs"][0]["results"]).is_empty()


def test_includes_summary_when_provided() -> None:
    """Include summary in rendered SARIF output."""
    summary = AISummary(overview="Test overview")
    result = render_fixes_sarif([], summary=summary)
    parsed = json.loads(result)
    assert_that(
        parsed["runs"][0]["properties"]["aiSummary"]["overview"],
    ).is_equal_to(
        "Test overview",
    )


# -- TestWriteSarif: Tests for SARIF file writing. ---------------------------


def test_writes_valid_sarif_file(tmp_path: Path) -> None:
    """Write a valid SARIF file to disk."""
    output = tmp_path / "results.sarif"
    s = AIFixSuggestion(
        file="src/main.py",
        line=10,
        code="B101",
        explanation="Replace assert",
        risk_level="low",
    )
    write_sarif([s], output_path=output)

    assert_that(output.exists()).is_true()
    parsed = json.loads(output.read_text())
    assert_that(parsed["version"]).is_equal_to(SARIF_VERSION)
    assert_that(parsed["runs"][0]["results"]).is_length(1)


def test_creates_parent_directories(tmp_path: Path) -> None:
    """Create parent directories when they do not exist."""
    output = tmp_path / "sub" / "dir" / "results.sarif"
    write_sarif([], output_path=output)
    assert_that(output.exists()).is_true()


def test_writes_with_summary(tmp_path: Path) -> None:
    """Write SARIF file including summary properties."""
    output = tmp_path / "results.sarif"
    summary = AISummary(overview="Summary text")
    write_sarif([], summary=summary, output_path=output)

    parsed = json.loads(output.read_text())
    assert_that(
        parsed["runs"][0]["properties"]["aiSummary"]["overview"],
    ).is_equal_to("Summary text")
