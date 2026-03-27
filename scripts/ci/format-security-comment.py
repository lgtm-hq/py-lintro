#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Format lintro osv_scanner JSON output as a security PR comment.

Reads lintro JSON output (from --output-format json) and extracts the
osv_scanner result to produce a markdown PR comment body with a
vulnerability table and suppression status table.

Usage:
    python3 scripts/ci/format-security-comment.py osv-results.json

Exit codes:
    0 - Success (markdown printed to stdout)
    1 - Invalid arguments or missing file
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _escape_md_cell(value: str) -> str:
    """Escape a string for safe use inside a Markdown table cell.

    Replaces pipe characters and strips newlines so the value cannot
    break table formatting.
    """
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", "")


def _fence_code_block(text: str) -> str:
    """Wrap text in a Markdown code fence safe against embedded backticks."""
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}\n{text}\n{fence}"


def format_comment(json_path: str) -> str | None:
    """Format osv-scanner JSON results as markdown.

    Args:
        json_path: Path to the lintro JSON output file.

    Returns:
        Markdown string for the PR comment body, or None on error.
        Error messages are written to stderr.
    """
    path = Path(json_path)
    if not path.exists():
        print(
            "No osv-results.json found — osv-scanner may not have run.",
            file=sys.stderr,
        )
        return None

    data = json.loads(path.read_text())
    results = data.get("results", [])

    # Find osv_scanner result (key is "tool" in lintro JSON output)
    osv_result = None
    for r in results:
        if r.get("tool") == "osv_scanner":
            osv_result = r
            break

    if osv_result is None:
        print("osv-scanner did not produce results.", file=sys.stderr)
        return None

    issues = osv_result.get("issues", [])
    ai_meta = osv_result.get("ai_metadata") or {}
    suppressions = ai_meta.get("suppressions", [])

    sections: list[str] = []

    sections.append("### 🔍 Checks Performed:")
    sections.append(
        "- **osv-scanner**: Scanned all lockfiles against the OSV database",
    )
    sections.append("")

    # Vulnerability table
    if issues:
        sections.append("### 🚨 Vulnerability Report:")
        sections.append("| Vulnerability | File |")
        sections.append("|---------------|------|")
        for issue in issues:
            # Lintro display format: message = "[GHSA-xxx] pkg@ver (fix: X.Y.Z)"
            msg = _escape_md_cell(issue.get("message", "?"))
            file = _escape_md_cell(issue.get("file", "?"))
            sections.append(f"| {msg} | `{file}` |")
        sections.append("")
        sections.append("### 🔧 Recommended Actions:")
        sections.append("1. Review the vulnerabilities above")
        sections.append("2. Update affected packages if fixes are available")
        sections.append(
            "3. If no fix is available, add a suppression to "
            ".osv-scanner.toml with an expiry date",
        )
    elif osv_result.get("success") is False:
        output_text = osv_result.get("output", "")
        sections.append("### ⚠️ Scanner Error:")
        sections.append(
            "osv-scanner encountered an error during scanning. "
            "Review the CI logs for details.",
        )
        if output_text:
            # Show first 500 chars of output for debugging context
            preview = output_text[:500]
            sections.append("")
            sections.append(_fence_code_block(preview))
    else:
        sections.append("No security vulnerabilities found in dependencies.")

    # Suppression table
    if suppressions:
        sections.append("")
        sections.append("### 🔇 Suppressed Vulnerabilities:")
        sections.append("| ID | Expires | Status | Reason |")
        sections.append("|----|---------|--------|--------|")
        for s in suppressions:
            sid = _escape_md_cell(s.get("id", "?"))
            expires = _escape_md_cell(s.get("ignore_until", "?"))
            status = _escape_md_cell(s.get("status", "?"))
            reason = _escape_md_cell(s.get("reason", ""))
            if status == "expired":
                sections.append(
                    f"| :warning: `{sid}` | **EXPIRED** {expires} "
                    f"| :warning: Expired | {reason} |",
                )
            elif status == "stale":
                sections.append(
                    f"| `{sid}` | {expires} "
                    f"| :warning: **Stale — safe to remove** | {reason} |",
                )
            else:
                sections.append(f"| `{sid}` | {expires} | Active | {reason} |")

    return "\n".join(sections)


def main() -> None:
    """Entry point."""
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <json_file>", file=sys.stderr)
        sys.exit(1)

    output = format_comment(sys.argv[1])
    if output is not None:
        print(output)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
