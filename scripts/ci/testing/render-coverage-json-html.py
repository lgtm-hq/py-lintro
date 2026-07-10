#!/usr/bin/env python3
"""Render a simple HTML coverage index from coverage.py JSON output.

The CI ``python-coverage`` artifact currently ships ``coverage.json`` (not the
binary ``.coverage`` data file). ``coverage html --data-file`` cannot read JSON
or Cobertura XML, so this helper builds a browsable summary for Pages bundling
when only the JSON report is available.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def _percent(summary: dict[str, Any]) -> float:
    """Return a display percentage from a coverage summary mapping.

    Args:
        summary: coverage.py file or totals summary dict.

    Returns:
        Covered percent in the range 0-100.
    """
    value = summary.get("percent_covered")
    if isinstance(value, (int, float)):
        return float(value)
    display = summary.get("percent_covered_display")
    if isinstance(display, str) and display.strip():
        try:
            return float(display.strip())
        except ValueError:
            return 0.0
    return 0.0


def render_coverage_json_html(
    *,
    coverage_data: dict[str, Any],
    output_dir: Path,
) -> Path:
    """Write ``index.html`` summarizing coverage.json into ``output_dir``.

    Args:
        coverage_data: Parsed coverage.py JSON report.
        output_dir: Directory that will contain ``index.html``.

    Returns:
        Path to the written ``index.html``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    totals = coverage_data.get("totals", {})
    files = coverage_data.get("files", {})
    overall = _percent(totals if isinstance(totals, dict) else {})

    rows: list[str] = []
    for path in sorted(files):
        entry = files[path]
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}
        pct = _percent(summary)
        covered = summary.get("covered_lines", 0)
        statements = summary.get("num_statements", 0)
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(path)}</code></td>"
            f"<td>{covered}/{statements}</td>"
            f"<td>{pct:.1f}%</td>"
            "</tr>",
        )

    body = "\n".join(rows) if rows else "<tr><td colspan='3'>No files</td></tr>"
    index_path = output_dir / "index.html"
    index_path.write_text(
        (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="utf-8" />\n'
            "  <title>Python coverage</title>\n"
            "  <style>\n"
            "    body { font-family: system-ui, sans-serif; margin: 2rem; }\n"
            "    table { border-collapse: collapse; width: 100%; }\n"
            "    th, td { border-bottom: 1px solid #ddd; padding: 0.4rem 0.6rem;"
            " text-align: left; }\n"
            "    th { background: #f4f4f4; }\n"
            "    code { font-size: 0.9em; }\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            "  <h1>Python coverage</h1>\n"
            f"  <p>Overall: <strong>{overall:.1f}%</strong></p>\n"
            "  <table>\n"
            "    <thead><tr><th>File</th><th>Lines</th><th>Covered</th></tr>"
            "</thead>\n"
            f"    <tbody>\n{body}\n    </tbody>\n"
            "  </table>\n"
            "</body>\n"
            "</html>\n"
        ),
        encoding="utf-8",
    )
    return index_path


def main() -> int:
    """CLI entrypoint.

    Returns:
        Process exit code.

    Raises:
        SystemExit: If coverage.json root is not a JSON object.
    """
    parser = argparse.ArgumentParser(
        description="Render coverage.json to a simple HTML index.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to coverage.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for index.html",
    )
    args = parser.parse_args()
    coverage_data = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(coverage_data, dict):
        raise SystemExit("coverage.json root must be an object")
    render_coverage_json_html(
        coverage_data=coverage_data,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
