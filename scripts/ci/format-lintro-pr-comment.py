#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Build Lintro PR comment content from a lintro run's ``report.md``.

The lint job uploads ``.lintro/`` as an artifact and the comment job downloads
it, so the formatter has a single source of truth: ``report.md`` produced by
``OutputManager``. The file wraps the ANSI-stripped console dump in a fenced
code block; the comment extracts the ``EXECUTION SUMMARY`` section to keep the
posted comment compact while preserving the on-disk artifact verbatim.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SUMMARY_HEADER = "EXECUTION SUMMARY"
MAX_FALLBACK_LINES = 50

# Case-sensitive: matches status-column cells (FAIL, ERROR) and the ❌ emoji,
# but not lowercase/title-case header labels like "Failed | 0".
_FAILURE_MARKER = re.compile(r"❌|\bFAIL\b|\bERROR\b")
# Non-zero issue counts in lintro's totals table.
_NONZERO_ISSUES = re.compile(
    r"(?:Total|Remaining)\s+Issues\s*\|\s*[1-9]",
)


@dataclass(slots=True)
class CommentPayload:
    """Structured PR comment data.

    Attributes:
        status: Short status label shown in the PR comment header.
        content: Markdown body content inserted below the header.
    """

    status: str
    content: str


def _read_text(path: Path) -> str:
    """Read UTF-8 text from a file.

    Args:
        path: File to read.

    Returns:
        File contents as text.
    """
    return path.read_text(encoding="utf-8")


def _extract_summary_section(text: str) -> str | None:
    """Extract the execution summary section from lintro output.

    Args:
        text: Raw lintro output (console dump or report.md body).

    Returns:
        The execution summary section if present, otherwise ``None``.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if SUMMARY_HEADER in line:
            return "\n".join(lines[index:]).strip()
    return None


def _tail_text(text: str, *, line_count: int = MAX_FALLBACK_LINES) -> str:
    """Return the last ``line_count`` lines of text.

    Args:
        text: Input text.
        line_count: Maximum number of trailing lines to keep.

    Returns:
        Trailing lines joined with newlines.
    """
    lines = text.splitlines()
    return "\n".join(lines[-line_count:]).strip()


def _markdown_code_block(text: str | None) -> str:
    """Render text inside a markdown code block with a safe fence.

    Args:
        text: Raw text to wrap in a code block.

    Returns:
        Markdown code block using a fence longer than any backtick run in text.
    """
    normalized = (text or "").rstrip("\n")
    backtick_runs = re.findall(r"`+", normalized)
    longest_run = max((len(run) for run in backtick_runs), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}\n{normalized}\n{fence}"


def _strip_report_md_wrapper(text: str) -> str:
    """Return the fenced console dump embedded in ``report.md``.

    ``report.md`` is rendered as ``# Lintro Report`` + metadata line + a single
    fenced code block. Tests and downstream consumers sometimes pass either the
    raw file contents or the already-unwrapped dump, so this helper accepts
    both.

    Args:
        text: Raw file contents.

    Returns:
        The console dump portion without the Markdown wrapper, or the original
        text when no fenced block is found.
    """
    fence_match = re.search(
        r"^```[^\n]*\n(.*?)\n```\s*$",
        text,
        re.DOTALL | re.MULTILINE,
    )
    if fence_match is not None:
        return fence_match.group(1)
    return text


def _infer_status_from_output(*, output: str, exit_code: str | None) -> str:
    """Infer a PR-comment status label from lint output.

    Args:
        output: Output block shown in the PR comment.
        exit_code: Optional exit code captured by the lint step.

    Returns:
        Status label for the PR comment header.
    """
    if exit_code is not None and exit_code != "0":
        return "⚠️ ISSUES FOUND"

    if _FAILURE_MARKER.search(output) or _NONZERO_ISSUES.search(output):
        return "⚠️ ISSUES FOUND"

    return "✅ PASSED"


def build_payload_from_report_md(
    *,
    report_md_path: Path,
    exit_code: str | None,
) -> CommentPayload:
    """Build a PR comment payload from a lintro ``report.md``.

    Args:
        report_md_path: Path to the run directory's ``report.md``.
        exit_code: Optional lint exit code captured in CI.

    Returns:
        Parsed comment payload.
    """
    raw = _read_text(report_md_path)
    body = _strip_report_md_wrapper(raw)
    output = _extract_summary_section(body) or _tail_text(body)
    if output.strip():
        status = _infer_status_from_output(output=output, exit_code=exit_code)
        content = f"### 📋 Results:\n{_markdown_code_block(output)}"
    else:
        status = "⚠️ CHECK LOGS"
        content = (
            "Lintro did not capture any output in `report.md`. "
            "Please inspect the workflow logs for the full failure context."
        )
    return CommentPayload(status=status, content=content)


def build_unavailable_payload(
    *,
    reason: str,
    details: str | None,
) -> CommentPayload:
    """Build a PR comment payload when ``report.md`` could not be located.

    Args:
        reason: Short explanation of why the artifact was missing.
        details: Optional diagnostic details to include.

    Returns:
        Parsed comment payload.
    """
    bullet_lines = [
        "Lintro results could not be recovered for this run.",
        "",
        "- **Primary issue:** `report.md` was unavailable in the comment job.",
        f"- **Recovery issue:** {reason}",
        "",
        "Please inspect the workflow logs for the full failure context.",
    ]

    if details:
        bullet_lines.extend(
            [
                "",
                "### 🧭 Recovery details:",
                _markdown_code_block(details),
            ],
        )

    return CommentPayload(
        status="⚠️ OUTPUT UNAVAILABLE",
        content="\n".join(bullet_lines).strip(),
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Raw command-line arguments.

    Returns:
        Parsed namespace.
    """
    parser = argparse.ArgumentParser(
        description="Build lintro PR comment content from report.md.",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--report-md",
        type=Path,
        help="Path to the lintro run directory's report.md.",
    )
    source_group.add_argument(
        "--fallback-reason",
        help="Reason the comment had to fall back to an unavailable-output notice.",
    )
    parser.add_argument(
        "--exit-code",
        default=None,
        help="Optional lint exit code captured by the CI step.",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        required=True,
        help="Path where the computed status label will be written.",
    )
    parser.add_argument(
        "--content-file",
        type=Path,
        required=True,
        help="Path where the computed markdown content will be written.",
    )
    parser.add_argument(
        "--details",
        default=None,
        help="Optional diagnostic details for unavailable-output fallback comments.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the formatter CLI.

    Args:
        argv: Optional command-line argument list.

    Returns:
        Process exit code.
    """
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if args.report_md is not None:
            payload = build_payload_from_report_md(
                report_md_path=args.report_md,
                exit_code=args.exit_code,
            )
        else:
            payload = build_unavailable_payload(
                reason=args.fallback_reason,
                details=args.details,
            )
    except FileNotFoundError as exc:
        print(f"Input file not found: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Failed to read input file: {exc}", file=sys.stderr)
        return 1

    # Write content before status so a failure cannot leave a stale status
    # file alongside missing content (consumers treat status as the signal).
    try:
        args.content_file.write_text(payload.content, encoding="utf-8")
    except OSError as exc:
        print(
            f"Failed to write content file {args.content_file}: {exc}",
            file=sys.stderr,
        )
        return 1
    try:
        args.status_file.write_text(payload.status, encoding="utf-8")
    except OSError as exc:
        print(
            f"Failed to write status file {args.status_file}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
