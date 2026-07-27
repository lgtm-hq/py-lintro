"""SARIF relocation parity guard (issue #724, PR 1 of 4).

``lintro/ai/output/sarif.py`` moved to ``lintro/utils/output/sarif/`` and the
public signatures were inverted so standard lint issues are the leading
positional argument. That relocation must be behaviour-preserving for the
non-AI path, which is the only path ``--output-format sarif`` exercises when
the AI layer is disabled.

The golden artifact in ``fixtures/sarif_standard_issues_pre_move.sarif`` was
produced by the pre-move renderer (``lintro/ai/output/sarif.py`` at
``0c91443e``) from the fixture below. The relocated renderer must reproduce it
byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.utils.output.sarif import (
    render_fixes_sarif,
    standard_issues_from_results,
    to_sarif,
    write_sarif,
)

_GOLDEN_PATH = (
    Path(__file__).parent / "fixtures" / "sarif_standard_issues_pre_move.sarif"
)

_DOC_URLS = {"E501": "https://docs.astral.sh/ruff/rules/line-too-long/"}


def _fixture_results() -> list[ToolResult]:
    """Build the tool results the golden artifact was generated from.

    Covers multiple tools, a shared rule code across tools, a rule with and
    without a ``helpUri``, and issues with and without a column.

    Returns:
        Tool results carrying parsed lint issues.
    """
    return [
        ToolResult(
            name="ruff",
            success=False,
            output="",
            issues=[
                RuffIssue(
                    file="src/alpha.py",
                    line=12,
                    column=5,
                    code="E501",
                    message="Line too long (100 > 88)",
                    fixable=False,
                ),
                RuffIssue(
                    file="src/alpha.py",
                    line=30,
                    column=0,
                    code="F401",
                    message="Unused import: os",
                    fixable=True,
                ),
            ],
        ),
        ToolResult(
            name="bandit",
            success=False,
            output="",
            issues=[
                RuffIssue(
                    file="src/beta.py",
                    line=7,
                    column=1,
                    code="E501",
                    message="Line too long (95 > 88)",
                    fixable=False,
                ),
            ],
        ),
    ]


def test_render_matches_pre_move_golden() -> None:
    """Render standard issues byte-identically to the pre-move renderer."""
    rendered = render_fixes_sarif(
        standard_issues_from_results(_fixture_results()),
        doc_urls=_DOC_URLS,
    )

    assert_that(rendered).is_equal_to(
        _GOLDEN_PATH.read_text(encoding="utf-8").rstrip("\n"),
    )


def test_write_matches_pre_move_golden(tmp_path: Path) -> None:
    """Write a SARIF file byte-identically to the pre-move renderer."""
    output = tmp_path / "results.sarif"

    write_sarif(
        standard_issues_from_results(_fixture_results()),
        output_path=output,
        doc_urls=_DOC_URLS,
    )

    assert_that(output.read_bytes()).is_equal_to(
        _GOLDEN_PATH.read_bytes(),
    )


def test_ai_enrichment_absent_leaves_no_ai_properties() -> None:
    """Emit no AI-specific properties when no enrichment is supplied."""
    sarif = to_sarif(
        standard_issues_from_results(_fixture_results()),
        doc_urls=_DOC_URLS,
    )

    assert_that(sarif["runs"][0]).does_not_contain_key("properties")
    for result in sarif["runs"][0]["results"]:
        assert_that(result["properties"]).does_not_contain_key("confidence")
        assert_that(result["properties"]).does_not_contain_key("riskLevel")
        assert_that(result).does_not_contain_key("fixes")


def test_golden_artifact_is_valid_sarif() -> None:
    """Confirm the golden artifact is a well-formed SARIF v2.1.0 document."""
    document = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))

    assert_that(document["version"]).is_equal_to("2.1.0")
    assert_that(document["runs"]).is_length(1)
    assert_that(document["runs"][0]["results"]).is_length(3)
