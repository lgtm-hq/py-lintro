"""Tests for the SARIF AI-enrichment boundary (issues #724, #1823).

``suggestions_from_results``/``summary_from_results`` live in
``lintro.ai.sarif_bridge`` because they construct AI models. Core SARIF
emitters never call them: since the execute/render split they receive an
already-built :class:`~lintro.models.core.sarif_enrichment.AISarifEnrichment`
as plain data, so core stays free of ``lintro.ai`` imports.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from assertpy import assert_that

import lintro
from lintro.ai.interface import sarif_enrichment_from_results
from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.models.core.sarif_enrichment import AISarifEnrichment
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


def _is_type_checking(test: ast.expr) -> bool:
    """Whether an ``if`` test is the ``TYPE_CHECKING`` guard.

    Args:
        test: The condition expression of an ``if`` statement.

    Returns:
        bool: True when the branch only runs for static type checkers.
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _is_ai_module(name: str) -> bool:
    """Whether a dotted module name is the AI package or lives inside it.

    Matched on a package boundary so a future ``lintro.aisomething`` module is
    not mistaken for ``lintro.ai``.

    Args:
        name: Dotted module name from an import statement.

    Returns:
        bool: True when the name refers to the AI package.
    """
    return name == "lintro.ai" or name.startswith("lintro.ai.")


def _imports_ai(node: ast.AST) -> bool:
    """Whether an AST node is a runtime import of :mod:`lintro.ai`.

    Args:
        node: Any node from the parsed module.

    Returns:
        bool: True when the node imports from the AI package.
    """
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if _is_ai_module(module):
            return True
        # ``from lintro import ai`` binds the AI package under a module named
        # ``lintro``, so the package name alone is not enough.
        return module == "lintro" and any(alias.name == "ai" for alias in node.names)
    if isinstance(node, ast.Import):
        return any(_is_ai_module(alias.name) for alias in node.names)
    return False


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from lintro.ai.interface import enhance_artifact", True),
        ("from lintro.ai import interface", True),
        ("import lintro.ai.sarif_bridge", True),
        ("from lintro import ai", True),
        ("from lintro import ai as ai_layer", True),
        ("from lintro.enums.action import Action", False),
        ("import lintro.utils.output", False),
        ("from lintro import enums", False),
    ],
)
def test_imports_ai_recognizes_every_runtime_import_form(
    source: str,
    expected: bool,
) -> None:
    """The boundary check must not be dodged by an alternate import spelling.

    Args:
        source: A single import statement.
        expected: Whether it brings the AI package into scope at runtime.
    """
    node = ast.parse(source).body[0]

    assert_that(_imports_ai(node)).is_equal_to(expected)


def test_core_render_path_never_imports_the_ai_layer() -> None:
    """No module under the core render/execute path may import ``lintro.ai``.

    The seam callables are gone (issue #1823); what replaces them is a plain
    data hand-off. Assert the boundary at the source level so a new import
    edge cannot creep back into the executor or the output writers.
    """
    package_root = Path(lintro.__file__).parent
    guarded = [
        package_root / "utils" / "tool_executor.py",
        *sorted((package_root / "utils" / "execution").rglob("*.py")),
        *sorted((package_root / "utils" / "output").rglob("*.py")),
    ]
    offenders: list[str] = []

    for source_path in guarded:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        # Only the ``if`` body is type-only; an ``else:`` branch under the
        # same guard runs at runtime and must still be checked.
        type_only = {
            child
            for node in ast.walk(tree)
            if isinstance(node, ast.If) and _is_type_checking(node.test)
            for statement in node.body
            for child in ast.walk(statement)
        }
        for node in ast.walk(tree):
            if node in type_only:
                continue
            if not _imports_ai(node):
                continue
            line = getattr(node, "lineno", 0)
            offenders.append(f"{source_path.relative_to(package_root)}:{line}")

    assert_that(offenders).described_as("core modules importing lintro.ai").is_empty()
