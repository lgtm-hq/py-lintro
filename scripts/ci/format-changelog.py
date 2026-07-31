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

# A "clean" code identifier: only identifier characters, at least one letter
# (so numeric group separators like ``1_000`` are ignored) and at least one
# underscore. Anchored to a whole token — callers strip surrounding punctuation
# and skip links/URLs/filenames first, so a match here is unambiguously a bare
# snake_case / SCREAMING_SNAKE_CASE identifier lifted from a commit subject.
_PURE_IDENT_RE = re.compile(r"^(?=[A-Za-z0-9_]*[A-Za-z])[A-Za-z0-9]*_[A-Za-z0-9_]*$")

# Spans that must be passed through untouched inside otherwise-plain prose: a
# Markdown inline link/image ``[text](dest)``, an autolink ``<url>``, a bare
# URL, or a dotted token such as a filename or domain (``format_changelog.py``,
# ``example.com``). Wrapping any of these would break the rendered link or path.
_SKIP_SPAN_RE = re.compile(
    r"""
    !?\[[^\]]*\]\([^)]*\)        # [text](dest) or ![alt](src)
    | <[^>\s]+>                  # <autolink>
    | (?:https?://|www\.)\S+     # bare URL
    | \S*[A-Za-z0-9]\.[A-Za-z0-9]\S*  # dotted token: foo_bar.py, a.b.c
    """,
    re.VERBOSE,
)

# Leading and trailing punctuation stripped from a whitespace-delimited word
# before testing it as an identifier, then re-attached around the code span.
_LEADING_PUNCT = "([{"
_TRAILING_PUNCT = ")]}.,;:!?"


def _wrap_words(prose: str) -> str:
    """Wrap bare underscore identifiers in a link-free, code-free prose span.

    Each whitespace-delimited word is stripped of surrounding punctuation and, if
    the remaining core is a clean identifier, wrapped in an inline code span.
    ``**bold**`` markers, ``*emphasis*``, and anything that is not purely an
    identifier are left untouched.

    Args:
        prose: Plain inline text with no code spans, links, URLs, or filenames.

    Returns:
        str: The prose with identifier words wrapped in backticks.
    """

    def _wrap_word(match: re.Match[str]) -> str:
        word = match.group(0)
        lead = ""
        trail = ""
        core = word
        while core and core[0] in _LEADING_PUNCT:
            lead += core[0]
            core = core[1:]
        while core and core[-1] in _TRAILING_PUNCT:
            trail = core[-1] + trail
            core = core[:-1]
        if _PURE_IDENT_RE.match(core):
            return f"{lead}`{core}`{trail}"
        return word

    return re.sub(r"\S+", _wrap_word, prose)


def _wrap_prose(segment: str) -> str:
    """Wrap identifiers in prose, leaving links, URLs, and filenames intact.

    Args:
        segment: Inline text known to contain no backtick code spans.

    Returns:
        str: The segment with bare identifiers wrapped in inline code spans.
    """
    parts: list[str] = []
    pos = 0
    for match in _SKIP_SPAN_RE.finditer(segment):
        parts.append(_wrap_words(segment[pos : match.start()]))
        parts.append(match.group(0))
        pos = match.end()
    parts.append(_wrap_words(segment[pos:]))
    return "".join(parts)


def _protect_line(
    line: str,
    in_code: bool,
    delim_len: int,
) -> tuple[str, bool, int]:
    """Wrap identifiers on one physical line, tracking inline-code-span state.

    Backtick code spans are preserved verbatim. Delimiter runs are matched by
    length (a span opened with N backticks closes only on a run of exactly N), so
    multi-backtick spans such as ``code`` are handled correctly. The open/closed
    state and the opening delimiter length are threaded through the return value
    so a span that continues onto the next physical line stays protected.

    Args:
        line: The physical line to transform.
        in_code: Whether an inline code span is already open from a prior line.
        delim_len: Backtick-run length that opened the currently-open span.

    Returns:
        tuple[str, bool, int]: The transformed line, the updated in-code flag,
        and the updated open-delimiter length.
    """
    parts: list[str] = []
    seg_start = 0
    index = 0
    length = len(line)
    while index < length:
        if line[index] != "`":
            index += 1
            continue
        run_end = index
        while run_end < length and line[run_end] == "`":
            run_end += 1
        run = run_end - index
        if not in_code:
            parts.append(_wrap_prose(line[seg_start:index]))
            parts.append(line[index:run_end])
            in_code = True
            delim_len = run
            seg_start = run_end
        elif run == delim_len:
            parts.append(line[seg_start:run_end])
            in_code = False
            delim_len = 0
            seg_start = run_end
        index = run_end
    if in_code:
        # Span continues onto the next line: emit the remainder verbatim.
        parts.append(line[seg_start:])
    else:
        parts.append(_wrap_prose(line[seg_start:]))
    return "".join(parts), in_code, delim_len


def _protect_code_identifiers(text: str) -> str:
    """Wrap bare underscore identifiers across a whole markdown document.

    ``markdownlint`` (and CommonMark) treat a leading or trailing underscore on
    a word as a potential emphasis delimiter, so a snake_case identifier lifted
    from a commit subject (e.g. ``_rotate_audit_log``) can pair with another
    stray underscore to open a spurious emphasis span and trip ``MD037`` ("spaces
    inside emphasis markers"). Rendering the identifier as an inline code span is
    both the correct presentation for code and immune to emphasis parsing, so the
    generated changelog stays fully lintable without excluding the file or
    disabling the rule.

    Fenced code blocks, headings, HTML comments, link reference definitions, and
    blank lines are passed through untouched, matching the reflow pass. Every
    other line — including list-item continuations and hard-break lines — is
    protected, with inline-code-span state carried across consecutive content
    lines so a span that wraps onto a following line is never rewritten. The
    transform is idempotent: identifiers already inside code spans are left
    alone.

    Args:
        text: The full markdown document.

    Returns:
        str: The document with bare identifiers wrapped in inline code spans.
    """
    out: list[str] = []
    fence_marker: str | None = None
    fence_length = 0
    in_code = False
    delim_len = 0
    for line in text.split("\n"):
        fence_match = _FENCE_RE.match(line)
        if fence_match is not None:
            marker = fence_match.group("marker")
            marker_char = marker[0]
            marker_length = len(marker)
            if fence_marker is None:
                fence_marker = marker_char
                fence_length = marker_length
            elif marker_char == fence_marker and marker_length >= fence_length:
                fence_marker = None
                fence_length = 0
            out.append(line)
            in_code = False
            delim_len = 0
            continue
        if fence_marker is not None:
            out.append(line)
            continue
        if (
            line.strip() == ""
            or _HEADING_RE.match(line)
            or _HTML_COMMENT_RE.match(line)
            or _LINK_REF_RE.match(line)
        ):
            out.append(line)
            # A block boundary ends any inline code span.
            in_code = False
            delim_len = 0
            continue
        protected, in_code, delim_len = _protect_line(line, in_code, delim_len)
        out.append(protected)
    return "\n".join(out)


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
    newline, matching ``prettier``. Before reflowing, bare underscore identifiers
    in commit-subject prose are wrapped in inline code spans so the generated
    changelog does not trip ``markdownlint`` ``MD037`` (see
    :func:`_protect_code_identifiers`).

    Args:
        text: The full markdown document.

    Returns:
        str: The formatted document.
    """
    lines = _protect_code_identifiers(text).split("\n")
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
