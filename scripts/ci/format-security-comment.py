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
    return (
        value.replace("|", "\\|")
        .replace("`", "\\`")
        .replace("\n", " ")
        .replace("\r", "")
    )


def _read_suppressions_from_toml() -> list[dict[str, object]]:
    """Read suppression entries from .osv-scanner.toml as a fallback.

    Returns:
        List of suppression entry dicts, or empty list if not available.
    """
    import tomllib

    toml_path = Path(".osv-scanner.toml")
    if not toml_path.exists():
        return []
    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        return [
            entry
            for entry in data.get("IgnoredVulns", [])
            if isinstance(entry, dict) and "id" in entry
        ]
    except (tomllib.TOMLDecodeError, OSError) as e:
        print(f"Warning: failed to parse {toml_path}: {e}", file=sys.stderr)
        return []


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

    try:
        content = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        print(f"Failed to read {path}: {e}", file=sys.stderr)
        return None

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON from {path}: {e}", file=sys.stderr)
        return None

    if not isinstance(data, dict):
        print(
            "Invalid JSON structure: top-level value is not an object",
            file=sys.stderr,
        )
        return None

    results = data.get("results", [])
    if not isinstance(results, list):
        print("Invalid JSON structure: 'results' is not a list", file=sys.stderr)
        return None

    # Find osv_scanner result (key is "tool" in lintro JSON output)
    osv_result = None
    for r in results:
        if isinstance(r, dict) and r.get("tool") == "osv_scanner":
            osv_result = r
            break

    if osv_result is None:
        print("osv-scanner did not produce results.", file=sys.stderr)
        return None

    ai_meta = osv_result.get("ai_metadata")
    # None means probe didn't run; [] means probe ran but found no suppressions
    probe_suppressions: list[dict[str, object]] | None = None
    if isinstance(ai_meta, dict) and isinstance(
        ai_meta.get("suppressions"),
        list,
    ):
        probe_suppressions = ai_meta["suppressions"]

    sections: list[str] = []

    sections.append("### 🔍 Checks Performed:")
    sections.append(
        "- **osv-scanner**: Scanned all lockfiles against the OSV database",
    )
    sections.append("")

    # Vulnerability table — use issues_count as the primary indicator
    # since the JSON writer may omit the issues list
    issues_count = osv_result.get("issues_count", 0)
    if issues_count > 0:
        issues_list = osv_result.get("issues", [])
        sections.append("### 🚨 Vulnerability Report:")
        sections.append("| Vulnerability | File |")
        sections.append("|---------------|------|")
        if issues_list:
            for issue in issues_list:
                if not isinstance(issue, dict):
                    continue
                msg = _escape_md_cell(str(issue.get("message") or "?"))
                file = _escape_md_cell(str(issue.get("file") or "?"))
                sections.append(f"| {msg} | `{file}` |")
        else:
            sections.append(
                f"| {issues_count} vulnerabilities found"
                " (details unavailable) | — |",
            )
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

    # Suppression table — prefer probe-derived status from scan result,
    # fall back to static entries from .osv-scanner.toml
    sections.append("")
    sections.append("### 🔇 Suppressed Vulnerabilities:")
    if probe_suppressions is not None:
        # Probe ran: show status (active/stale/expired) from scan result
        if not probe_suppressions:
            sections.append("No suppressions configured.")
        else:
            sections.append("| ID | Expires | Status | Reason |")
            sections.append("|----|---------|--------|--------|")
        for s in probe_suppressions:
            if not isinstance(s, dict):
                continue
            sid = _escape_md_cell(str(s.get("id", "?")))
            expires = _escape_md_cell(str(s.get("ignore_until", "?")))
            status = str(s.get("status", "active"))
            reason = _escape_md_cell(str(s.get("reason", "")))
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
    else:
        # No probe data: fall back to static TOML entries
        toml_suppressions = _read_suppressions_from_toml()
        if toml_suppressions:
            sections.append("| ID | Expires | Reason |")
            sections.append("|----|---------|--------|")
            for s in toml_suppressions:
                sid = _escape_md_cell(str(s.get("id", "?")))
                expires = _escape_md_cell(str(s.get("ignoreUntil", "?")))
                reason = _escape_md_cell(str(s.get("reason", "")))
                sections.append(f"| `{sid}` | {expires} | {reason} |")
        else:
            sections.append("No suppressions configured.")

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
