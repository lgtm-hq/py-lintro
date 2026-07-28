"""Tests for the SARIF AI-enrichment seam introduced by issue #724.

``suggestions_from_results``/``summary_from_results`` moved into
``lintro.ai.sarif_bridge`` because they construct AI models. Core SARIF
emitters reach them only through
:class:`~lintro.models.core.ai_seam.AISarifEnricher`, so core stays free of
``lintro.ai`` imports.
"""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

from lintro.ai.interface import sarif_enrichment_from_results
from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.models.core.ai_seam import AISarifEnrichment
from lintro.models.core.tool_result import ToolResult
from lintro.utils.output.file_writer import write_output_file

_METADATA = {
    "summary": {"overview": "Two risky asserts"},
    "fix_suggestions": [
        {
            "file": "src/main.py",
            "line": 3,
            "code": "B101",
            "tool_name": "bandit",
            "explanation": "Replace assert",
            "confidence": "high",
        },
    ],
}


def test_facade_reconstructs_enrichment_from_metadata() -> None:
    """The facade rebuilds AI objects off the renamed ``metadata`` field."""
    result = ToolResult(name="bandit", success=False, metadata=dict(_METADATA))

    enrichment = sarif_enrichment_from_results(all_results=[result])

    assert_that(enrichment.suggestions).is_length(1)
    assert_that(enrichment.suggestions[0].file).is_equal_to("src/main.py")
    assert_that(enrichment.summary).is_not_none()
    assert_that(enrichment.summary.overview).is_equal_to(  # type: ignore[union-attr]  # assertpy is_not_none narrows this
        "Two risky asserts",
    )


def test_facade_returns_empty_enrichment_without_metadata() -> None:
    """No metadata means no enrichment, not an error."""
    enrichment = sarif_enrichment_from_results(
        all_results=[ToolResult(name="ruff", success=True)],
    )

    assert_that(enrichment.suggestions).is_empty()
    assert_that(enrichment.summary).is_none()


def test_write_output_file_renders_injected_enrichment(tmp_path: Path) -> None:
    """SARIF output carries AI enrichment supplied through the seam."""
    result = ToolResult(name="bandit", success=False, metadata=dict(_METADATA))
    output_path = tmp_path / "results.sarif.json"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.SARIF,
        all_results=[result],
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
        ai_enrichment=sarif_enrichment_from_results(all_results=[result]),
    )

    document = json.loads(output_path.read_text(encoding="utf-8"))
    payload = json.dumps(document)
    assert_that(payload).contains("Two risky asserts")
    assert_that(payload).contains("src/main.py")


def test_write_output_file_without_enrichment_omits_ai(tmp_path: Path) -> None:
    """Omitting the seam renders standard-only SARIF."""
    result = ToolResult(name="bandit", success=False, metadata=dict(_METADATA))
    output_path = tmp_path / "results.sarif.json"

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.SARIF,
        all_results=[result],
        action=Action.CHECK,
        total_issues=1,
        total_fixed=0,
    )

    payload = output_path.read_text(encoding="utf-8")
    assert_that(payload).does_not_contain("Two risky asserts")


def test_empty_enrichment_is_the_no_ai_default() -> None:
    """The core-owned value object defaults to nothing to render."""
    enrichment = AISarifEnrichment()

    assert_that(enrichment.suggestions).is_empty()
    assert_that(enrichment.summary).is_none()
