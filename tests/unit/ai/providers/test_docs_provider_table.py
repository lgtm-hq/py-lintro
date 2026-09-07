"""The provider tables in ``docs/ai-features.md`` are asserted from metadata.

Acceptance criterion 4 of #2308: the published provider table is a snapshot of
the same records doctor, pricing and validation read, so a changed default or a
new priced model updates the docs or fails the suite — it can no longer quietly
disagree with the code. There is no code generator; this test is the update
path, printing the table to paste back.

Both tables are compared cell by cell after normalising whitespace, so
prettier's column padding is free to differ from what the renderer emits. A
failure prints the rows to paste back between the ``BEGIN``/``END GENERATED``
markers.
"""

from __future__ import annotations

import re
from pathlib import Path

from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.registry import all_metadata

#: The published document under test.
_DOC = Path(__file__).resolve().parents[4] / "docs" / "ai-features.md"

#: Column titles the published tables must carry.
_PROVIDER_HEADER = (
    "Provider",
    "Default model",
    "API key env",
    "Transports",
    "CLI binary",
)
_PRICING_HEADER = ("Provider", "Model", "Input", "Output")


def _marked_block(*, name: str) -> str:
    """Return the document text between one pair of generation markers.

    Args:
        name: Marker name, e.g. ``provider-table``.

    Returns:
        The text between the markers.

    Raises:
        AssertionError: If the markers are missing, so a silently dropped
            marker fails loudly instead of asserting on nothing.
    """
    pattern = re.compile(
        rf"<!-- BEGIN SNAPSHOT: {re.escape(name)} -->(.*?)<!-- END SNAPSHOT: {re.escape(name)} -->",
        re.DOTALL,
    )
    match = pattern.search(_DOC.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError(f"docs/ai-features.md has no '{name}' generated block")
    return match.group(1)


def _table_rows(*, block: str) -> list[tuple[str, ...]]:
    """Parse a markdown table into whitespace-normalised cell tuples.

    Args:
        block: Markdown text holding exactly one table.

    Returns:
        Every content row as a cell tuple, header first; the ``---`` separator
        is dropped, so prettier's padding churn is not a failure.
    """
    rows: list[tuple[str, ...]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
        if all(set(cell) <= {"-", ":"} and cell for cell in cells):
            continue
        rows.append(cells)
    return rows


def _transport_cell(*, supported: frozenset[AITransport], default: AITransport) -> str:
    """Render the transports cell for one provider.

    Args:
        supported: Transports the provider serves.
        default: The transport lintro documents for it.

    Returns:
        Backticked transport names in a stable order, the default annotated.
    """
    ordered = sorted(supported, key=lambda item: item.value)
    return ", ".join(
        f"`{item.value}` (default)" if item is default else f"`{item.value}`"
        for item in ordered
    )


def _expected_provider_rows() -> list[tuple[str, ...]]:
    """Render the provider table body from plugin metadata.

    Returns:
        One row per provider, in enum declaration order.
    """
    return [
        (
            record.display_name,
            f"`{record.default_model}`",
            f"`{record.default_api_key_env}`",
            _transport_cell(
                supported=record.supported_transports,
                default=record.default_transport,
            ),
            f"`{record.cli_binary}`" if record.cli_binary else "—",
        )
        for record in all_metadata().values()
    ]


def _expected_pricing_rows() -> list[tuple[str, ...]]:
    """Render the model-pricing table body from plugin metadata.

    Returns:
        One row per priced model, grouped by provider in enum declaration
        order and by the provider's own declaration order within that.
    """
    rows: list[tuple[str, ...]] = []
    for record in all_metadata().values():
        for model, pricing in record.pricing.items():
            rows.append(
                (
                    record.display_name,
                    f"`{model}`",
                    f"${pricing.input_per_million:.2f}",
                    f"${pricing.output_per_million:.2f}",
                ),
            )
    return rows


def _paste_back(*, header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """Render a full markdown table for a failure message.

    Args:
        header: Column titles.
        rows: Body rows.

    Returns:
        A markdown table the maintainer can paste between the markers.
    """
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def test_docs_provider_table_matches_plugin_metadata() -> None:
    """The published provider table restates exactly what the plugins declare."""
    expected = _expected_provider_rows()
    header, *actual = _table_rows(block=_marked_block(name="provider-table"))

    assert_that(header).is_equal_to(_PROVIDER_HEADER)
    assert_that(actual).described_as(
        "docs/ai-features.md provider table is stale; replace the generated "
        "block with:\n" + _paste_back(header=_PROVIDER_HEADER, rows=expected),
    ).is_equal_to(expected)


def test_docs_model_pricing_table_matches_plugin_metadata() -> None:
    """The published pricing table restates exactly what the plugins declare."""
    expected = _expected_pricing_rows()
    header, *actual = _table_rows(block=_marked_block(name="model-pricing-table"))

    assert_that(header).is_equal_to(_PRICING_HEADER)
    assert_that(actual).described_as(
        "docs/ai-features.md model pricing table is stale; replace the "
        "generated block with:\n" + _paste_back(header=_PRICING_HEADER, rows=expected),
    ).is_equal_to(expected)
