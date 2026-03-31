"""Grid output style implementation."""

from typing import Any

from lintro.formatters.core.format_registry import OutputStyle

# Try to import tabulate
try:
    from tabulate import tabulate

    TABULATE_AVAILABLE = True
except ImportError:
    TABULATE_AVAILABLE = False


class GridStyle(OutputStyle):
    """Output format that renders data as a formatted grid table.

    This style creates a nicely formatted table with proper column alignment
    and borders, similar to what you might see in a terminal or console.
    """

    def format(
        self,
        columns: list[str],
        rows: list[list[Any]],
        tool_name: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Format a table given columns and rows as a grid.

        Args:
            columns: List of column header names.
            rows: List of row values (each row is a list of cell values).
            tool_name: Optional tool name to include in context.
            **kwargs: Extra options ignored by this formatter.

        Returns:
            Formatted data as grid table string.
        """
        if not rows:
            return ""

        # Use tabulate if available
        if TABULATE_AVAILABLE:
            # Provide sane defaults for alignment and column widths to avoid
            # terminal wrapping that misaligns the grid. We keep most columns
            # left-aligned, numeric columns right-aligned, and cap wide
            # columns like File/Message.
            colalign_map = {
                "Line": "right",
                "Column": "right",
                "Fixable": "center",
            }
            colalign = [colalign_map.get(col, "left") for col in columns]

            # Cap very wide columns so tabulate wraps within cells, preserving
            # alignment instead of letting the terminal wrap mid-grid.
            width_map: dict[str, int] = {
                "File": 48,
                "Message": 64,
                "Code": 12,
                "Line": 8,
                "Column": 8,
                "Fixable": 8,
                "Docs": 52,
            }
            maxcolwidths = [width_map.get(col) for col in columns]

            return tabulate(
                tabular_data=rows,
                headers=columns,
                tablefmt="grid",
                stralign="left",
                numalign="right",
                colalign=colalign,
                maxcolwidths=maxcolwidths,
                disable_numparse=True,
            )

        # Fallback to simple format when tabulate is not available
        if not columns:
            return ""

        # Build the header
        header = " | ".join(columns)
        separator = "-" * len(header)

        # Build the rows
        formatted_rows = []
        for row in rows:
            # Ensure row has same number of elements as columns
            padded_row = row + [""] * (len(columns) - len(row))
            formatted_rows.append(" | ".join(str(cell) for cell in padded_row))

        # Combine all parts
        result = [header, separator] + formatted_rows
        return "\n".join(result)
