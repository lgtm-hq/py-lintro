"""End-to-end integration tests for the doc_url feature.

Tests the full pipeline: tool plugin → enrichment → formatted output,
verifying that doc_url flows correctly through all layers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.output_format import OutputFormat
from lintro.formatters.formatter import format_issues
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.tools.definitions.ruff import RuffPlugin
from lintro.utils.output.file_writer import write_output_file

# Relies on internal enrichment function to simulate the post-execution
# doc_url population step without running actual tool subprocesses.
from lintro.utils.tool_executor import _enrich_issues_with_doc_urls


@pytest.fixture
def enriched_ruff_result() -> ToolResult:
    """Create a ToolResult with RuffIssues and enriched doc_urls.

    Returns:
        ToolResult with doc_url-enriched issues.
    """
    issues = [
        RuffIssue(
            file="src/main.py",
            line=10,
            column=5,
            code="E501",
            message="Line too long (120 > 88)",
        ),
        RuffIssue(
            file="src/utils.py",
            line=3,
            column=1,
            code="F401",
            message="os imported but unused",
        ),
    ]
    result = ToolResult(
        name="ruff",
        success=False,
        output="Issues found",
        issues_count=len(issues),
        issues=issues,
    )

    # Simulate the enrichment step that tool_executor performs.
    # Relies on internal cache structure (_rule_name_cache) to avoid
    # subprocess calls — update if RuffPlugin caching is refactored.
    plugin = RuffPlugin()
    plugin._rule_name_cache["E501"] = "line-too-long"
    plugin._rule_name_cache["F401"] = "unused-import"
    _enrich_issues_with_doc_urls(plugin, result)

    return result


# =============================================================================
# Grid output
# =============================================================================


def test_grid_output_contains_docs_column_and_urls(
    enriched_ruff_result: ToolResult,
) -> None:
    """Grid format includes Docs column with URLs when doc_url is set.

    Args:
        enriched_ruff_result: Enriched ToolResult fixture.
    """
    assert enriched_ruff_result.issues is not None
    output = format_issues(enriched_ruff_result.issues, output_format="grid")

    assert_that(output).contains("Docs")
    assert_that(output).contains("line-too-long")
    assert_that(output).contains("unused-import")


def test_grid_output_omits_docs_when_no_urls() -> None:
    """Grid format omits Docs column when no issues have doc_url."""
    issues: list[RuffIssue] = [
        RuffIssue(
            file="foo.py",
            line=1,
            code="E501",
            message="test",
        ),
    ]
    output = format_issues(issues, output_format="grid")

    assert_that(output).does_not_contain("Docs")


# =============================================================================
# JSON output
# =============================================================================


def test_json_output_contains_doc_url(
    tmp_path: Path,
    enriched_ruff_result: ToolResult,
) -> None:
    """JSON output includes doc_url field on enriched issues.

    Args:
        tmp_path: Temporary directory for test output.
        enriched_ruff_result: Enriched ToolResult fixture.
    """
    json_path = tmp_path / "report.json"

    write_output_file(
        output_path=str(json_path),
        output_format=OutputFormat.JSON,
        all_results=[enriched_ruff_result],
        action=Action.CHECK,
        total_issues=2,
        total_fixed=0,
    )

    content = json.loads(json_path.read_text())
    issues = content["results"][0]["issues"]
    assert_that(issues).is_length(2)
    assert_that(issues[0]["doc_url"]).contains("line-too-long")
    assert_that(issues[1]["doc_url"]).contains("unused-import")


# =============================================================================
# Markdown output
# =============================================================================


def test_markdown_output_contains_clickable_links(
    tmp_path: Path,
    enriched_ruff_result: ToolResult,
) -> None:
    """Markdown output renders doc_url as clickable [docs](url) links.

    Args:
        tmp_path: Temporary directory for test output.
        enriched_ruff_result: Enriched ToolResult fixture.
    """
    md_path = tmp_path / "report.md"

    write_output_file(
        output_path=str(md_path),
        output_format=OutputFormat.MARKDOWN,
        all_results=[enriched_ruff_result],
        action=Action.CHECK,
        total_issues=2,
        total_fixed=0,
    )

    content = md_path.read_text()
    assert_that(content).contains("| Docs |")
    assert_that(content).contains(
        "[docs](https://docs.astral.sh/ruff/rules/line-too-long/)",
    )


# =============================================================================
# CSV output
# =============================================================================


def test_csv_output_contains_doc_url_column(
    tmp_path: Path,
    enriched_ruff_result: ToolResult,
) -> None:
    """CSV output includes doc_url column with URLs.

    Args:
        tmp_path: Temporary directory for test output.
        enriched_ruff_result: Enriched ToolResult fixture.
    """
    csv_path = tmp_path / "report.csv"

    write_output_file(
        output_path=str(csv_path),
        output_format=OutputFormat.CSV,
        all_results=[enriched_ruff_result],
        action=Action.CHECK,
        total_issues=2,
        total_fixed=0,
    )

    content = csv_path.read_text()
    assert_that(content).contains("doc_url")
    assert_that(content).contains(
        "https://docs.astral.sh/ruff/rules/line-too-long/",
    )


# =============================================================================
# DocUrlTemplate enum
# =============================================================================


def test_template_format_with_code() -> None:
    """Templates with {code} produce correct URLs when formatted."""
    url = DocUrlTemplate.RUFF.format(code="line-too-long")
    assert_that(url).is_equal_to("https://docs.astral.sh/ruff/rules/line-too-long/")


def test_static_template_unchanged() -> None:
    """Templates without {code} are usable as plain strings."""
    url = str(DocUrlTemplate.ACTIONLINT)
    assert_that(url).is_equal_to(
        "https://github.com/rhysd/actionlint/blob/main/docs/checks.md",
    )


def test_osv_advisory_url() -> None:
    """OSV template produces per-vulnerability URLs."""
    url = DocUrlTemplate.OSV.format(code="GHSA-c3g4-w6cv-6v7h")
    assert_that(url).is_equal_to(
        "https://osv.dev/vulnerability/GHSA-c3g4-w6cv-6v7h",
    )


def test_cargo_audit_advisory_url() -> None:
    """Cargo-audit template produces per-advisory URLs."""
    url = DocUrlTemplate.CARGO_AUDIT.format(code="RUSTSEC-2021-0124")
    assert_that(url).is_equal_to(
        "https://rustsec.org/advisories/RUSTSEC-2021-0124",
    )


# =============================================================================
# SARIF helpUri
# =============================================================================


def test_sarif_includes_help_uri() -> None:
    """SARIF rule descriptors include helpUri from doc_urls map."""
    from lintro.ai.models import AIFixSuggestion
    from lintro.ai.output.sarif import to_sarif

    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=10,
        code="E501",
        tool_name="ruff",
        original_code="x = 1",
        suggested_code="x = 1",
    )

    doc_urls = {"E501": "https://docs.astral.sh/ruff/rules/line-too-long/"}
    sarif = to_sarif([suggestion], doc_urls=doc_urls)

    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert_that(rules).is_length(1)
    assert_that(rules[0]["helpUri"]).is_equal_to(
        "https://docs.astral.sh/ruff/rules/line-too-long/",
    )


def test_sarif_omits_help_uri_when_no_doc_urls() -> None:
    """SARIF rule descriptors omit helpUri when no doc_urls provided."""
    from lintro.ai.models import AIFixSuggestion
    from lintro.ai.output.sarif import to_sarif

    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=10,
        code="E501",
        tool_name="ruff",
        original_code="x = 1",
        suggested_code="x = 1",
    )

    sarif = to_sarif([suggestion])

    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert_that(rules[0]).does_not_contain_key("helpUri")
