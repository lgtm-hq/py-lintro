#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Format the auto-generated ``CHANGELOG.md`` to be lintro-compliant.

The release Version-PR generator (lgtm-ci ``reusable-release-version-pr``)
writes ``CHANGELOG.md`` with unwrapped release-note lines that exceed the
repository's 88-column markdown budget, so ``markdownlint`` (``MD013``) and
``prettier`` (``proseWrap: always``) reject it. This module reflows the file's
prose and list items to 88 columns using the exact greedy word-wrap that
``prettier`` applies to markdown, producing byte-for-byte identical output while
requiring nothing beyond the standard library.

It is wired into ``.github/workflows/release-version-pr.yml`` via the reusable
workflow's ``version-update-script`` input, which runs after the CHANGELOG is
written and before the version PR is committed. That job has no Node toolchain
and blocks npm egress, so a pure-standard-library formatter is used here instead
of shelling out to ``prettier``.

Run standalone to format the repository CHANGELOG in place::

    python scripts/ci/format-changelog.py [PATH]

``PATH`` defaults to the ``CHANGELOG_PATH`` environment variable, then to
``CHANGELOG.md`` relative to the current working directory (the workspace root
under GitHub Actions).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Wrap width. Kept in sync with the ``*.md`` overrides in ``.prettierrc.json``
# (``printWidth: 88``) and ``.markdownlint-cli2.jsonc`` (``MD013.line_length``).
WRAP_WIDTH = 88

_LIST_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>[-*+]|\d+[.)]) (?P<text>.*)$")
_HEADING_RE = re.compile(r"^\s{0,3}#")
_HTML_COMMENT_RE = re.compile(r"^\s*<!--")
_LINK_REF_RE = re.compile(r"^\s*\[[^\]]+\]:\s")
_FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>`{3,}|~{3,})")


def _tokenize(text: str) -> list[str]:
    """Split text into wrap tokens, treating inline code spans as atomic.

    ``prettier`` never inserts a line break inside an inline code span
    (`` `...` ``), so spaces within backticks are preserved as part of a single
    token rather than acting as break points.

    Args:
        text: The raw inline text of a paragraph or list item.

    Returns:
        list[str]: Non-empty tokens separated by wrap-eligible whitespace.
    """
    tokens: list[str] = []
    current = ""
    in_code = False
    for char in text:
        if char == "`":
            in_code = not in_code
            current += char
        elif char == " " and not in_code:
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


def _wrap(tokens: list[str], first_prefix: str, cont_prefix: str) -> list[str]:
    """Greedily wrap tokens to :data:`WRAP_WIDTH`, mirroring ``prettier``.

    A token that alone exceeds the width is never broken; it is placed on its
    own line, matching ``prettier``'s handling of long URLs and code spans.

    Args:
        tokens: Wrap tokens produced by :func:`_tokenize`.
        first_prefix: Prefix for the first emitted line (e.g. ``"- "``).
        cont_prefix: Prefix for continuation lines (hanging indent).

    Returns:
        list[str]: The wrapped physical lines.
    """
    lines: list[str] = []
    current = first_prefix
    started = False
    for token in tokens:
        if not started:
            current = first_prefix + token
            started = True
        elif len(current) + 1 + len(token) <= WRAP_WIDTH:
            current = f"{current} {token}"
        else:
            lines.append(current)
            current = cont_prefix + token
    lines.append(current)
    return lines


def format_changelog(text: str) -> str:
    """Reflow markdown prose and list items to the wrap width.

    Headings, blank lines, HTML comments, link reference definitions, and fenced
    code blocks are passed through unchanged. Consecutive blank lines are
    collapsed to a single blank line and the result ends with exactly one
    newline, matching ``prettier``.

    Args:
        text: The full markdown document.

    Returns:
        str: The formatted document.
    """
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    total = len(lines)
    fence_marker: str | None = None
    fence_length = 0
    while index < total:
        line = lines[index]
        fence_match = _FENCE_RE.match(line)
        if fence_match is not None:
            marker = fence_match.group("marker")
            marker_char = marker[0]
            marker_length = len(marker)
            if fence_marker is None:
                fence_marker = marker_char
                fence_length = marker_length
                out.append(line.rstrip())
                index += 1
                continue
            if marker_char == fence_marker and marker_length >= fence_length:
                fence_marker = None
                fence_length = 0
                out.append(line.rstrip())
                index += 1
                continue
        if fence_marker is not None:
            # Preserve fenced content byte-for-byte, including blank-line runs.
            out.append(line)
            index += 1
            continue
        if (
            line.strip() == ""
            or _HEADING_RE.match(line)
            or _HTML_COMMENT_RE.match(line)
            or _LINK_REF_RE.match(line)
        ):
            out.append(line.rstrip())
            index += 1
            continue
        list_match = _LIST_RE.match(line)
        if list_match:
            indent = list_match.group("indent")
            marker = list_match.group("marker")
            first_prefix = f"{indent}{marker} "
            cont_prefix = " " * len(first_prefix)
            tokens = _tokenize(list_match.group("text"))
            index += 1
            while index < total:
                nxt = lines[index]
                if nxt.strip() == "":
                    break
                if _LIST_RE.match(nxt) and not nxt.startswith(cont_prefix + " "):
                    break
                if nxt.startswith(cont_prefix) or nxt.startswith(indent + " "):
                    # Keep hard-break lines separate so trailing ``  `` / ``\``
                    # markers are not stripped by flatten-and-rewrap.
                    if nxt.rstrip().endswith("\\") or nxt.rstrip("\n").endswith("  "):
                        break
                    tokens.extend(_tokenize(nxt.strip()))
                    index += 1
                else:
                    break
            out.extend(_wrap(tokens, first_prefix, cont_prefix))
            continue
        paragraph: list[str] = []
        while index < total:
            candidate = lines[index]
            if (
                candidate.strip() == ""
                or _HEADING_RE.match(candidate)
                or _HTML_COMMENT_RE.match(candidate)
                or _LINK_REF_RE.match(candidate)
                or _LIST_RE.match(candidate)
                or _FENCE_RE.match(candidate)
            ):
                break
            if candidate.rstrip().endswith("\\") or candidate.rstrip("\n").endswith(
                "  ",
            ):
                # Emit any accumulated prose, then keep the hard-break line as-is.
                if paragraph:
                    out.extend(_wrap(paragraph, "", ""))
                    paragraph = []
                out.append(candidate.rstrip("\n"))
                index += 1
                break
            paragraph.extend(_tokenize(candidate.strip()))
            index += 1
        if paragraph:
            out.extend(_wrap(paragraph, "", ""))

    collapsed: list[str] = []
    in_fence_collapse = False
    fence_char: str | None = None
    fence_len = 0
    for line in out:
        fence_match = _FENCE_RE.match(line)
        if fence_match is not None:
            marker = fence_match.group("marker")
            marker_char = marker[0]
            marker_length = len(marker)
            if not in_fence_collapse:
                in_fence_collapse = True
                fence_char = marker_char
                fence_len = marker_length
            elif marker_char == fence_char and marker_length >= fence_len:
                in_fence_collapse = False
                fence_char = None
                fence_len = 0
            collapsed.append(line)
            continue
        if line == "" and collapsed and collapsed[-1] == "" and not in_fence_collapse:
            continue
        collapsed.append(line)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    return "\n".join(collapsed) + "\n"


def _resolve_path(argv: list[str]) -> Path:
    """Resolve the CHANGELOG path from argv, env, then the default.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        Path: The changelog path to format.
    """
    if argv:
        return Path(argv[0])
    return Path(os.environ.get("CHANGELOG_PATH", "CHANGELOG.md"))


def main(argv: list[str]) -> int:
    """Format the changelog in place, writing only when content changes.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        int: Process exit code. Always ``0``: a missing changelog is a
        non-fatal skip (warning only) so the release Version-PR job is not
        blocked when the file is absent.
    """
    path = _resolve_path(argv)
    if not path.is_file():
        print(f"::warning::CHANGELOG not found, skipping format: {path}")
        return 0
    original = path.read_text(encoding="utf-8")
    formatted = format_changelog(original)
    if formatted != original:
        path.write_text(formatted, encoding="utf-8")
        print(f"Formatted {path} to {WRAP_WIDTH}-column markdown.")
    else:
        print(f"{path} already formatted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
