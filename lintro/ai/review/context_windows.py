"""Windowing helpers for the repository context section (#2714).

Split out of :mod:`lintro.ai.review.repo_context` for the module size
ratchet: hunk ranges from a unified diff, and cutting a file that does not
fit its budget share to the innermost definitions (Python, via ``ast``) or
line windows around its hunks.
"""

from __future__ import annotations

import ast
import re

from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.token_budget import estimate_tokens

__all__ = ["fit_content", "hunk_ranges"]

#: Lines of surrounding content kept around each hunk in a non-Python file.
_LINE_WINDOW = 30
#: Lines of context kept around a Python definition window.
_DEF_PADDING = 2
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def hunk_ranges(*, diff: str) -> dict[str, tuple[tuple[int, int], ...]]:
    """Return per-file new-side hunk line ranges of a unified diff."""
    ranges: dict[str, tuple[tuple[int, int], ...]] = {}
    for path, section in split_unified_diff_by_file(unified_diff=diff).items():
        spans = []
        for match in _HUNK_HEADER.finditer(section):
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            spans.append((start, start + max(count, 1) - 1))
        if spans:
            ranges[path] = tuple(spans)
    return ranges


def fit_content(
    *,
    path: str,
    content: str,
    hunks: tuple[tuple[int, int], ...],
    allowance: int,
) -> tuple[str, bool]:
    """Return the whole file when it fits, else windows around the hunks."""
    if estimate_tokens(content) <= allowance:
        return content, False
    lines = content.splitlines()
    if not hunks:
        return "", True
    spans = (
        _python_definition_spans(content=content, hunks=hunks, total=len(lines))
        if path.endswith((".py", ".pyi"))
        else None
    ) or [
        (max(1, start - _LINE_WINDOW), min(len(lines), end + _LINE_WINDOW))
        for start, end in hunks
    ]
    text = _render_spans(lines=lines, spans=_merge_spans(spans))
    # A window set that still does not fit shrinks to the hunks themselves.
    if estimate_tokens(text) > allowance:
        tight = _merge_spans(
            [
                (max(1, s - _DEF_PADDING), min(len(lines), e + _DEF_PADDING))
                for s, e in hunks
            ],
        )
        text = _render_spans(lines=lines, spans=tight)
    return text, True


def _python_definition_spans(
    *,
    content: str,
    hunks: tuple[tuple[int, int], ...],
    total: int,
) -> list[tuple[int, int]] | None:
    """Return the innermost enclosing definition span for each hunk.

    A hunk inside a method keeps the method, not the class; a hunk inside a
    nested function keeps the nested function. Decorators count as part of
    the definition. A hunk enclosed by no definition (module level, or one
    spanning several definitions) keeps a line window instead.

    Args:
        content: Python source text.
        hunks: New-file line ranges of the hunks.
        total: Line count of the file.

    Returns:
        Unmerged spans, or ``None`` when the source does not parse.
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None
    definitions: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min((d.lineno for d in node.decorator_list), default=node.lineno)
        end = getattr(node, "end_lineno", None) or node.lineno
        definitions.append((start, end))
    spans: list[tuple[int, int]] = []
    for hs, he in hunks:
        enclosing = [(s, e) for s, e in definitions if s <= hs and he <= e]
        if enclosing:
            start, end = min(enclosing, key=lambda span: span[1] - span[0])
            spans.append((max(1, start - _DEF_PADDING), min(total, end + _DEF_PADDING)))
        else:
            spans.append((max(1, hs - _LINE_WINDOW), min(total, he + _LINE_WINDOW)))
    return spans or None


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent line spans, in order."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _render_spans(*, lines: list[str], spans: list[tuple[int, int]]) -> str:
    """Render numbered line spans separated by ellipsis markers."""
    out: list[str] = []
    for index, (start, end) in enumerate(spans):
        if index:
            out.append("…")
        out.append(f"# lines {start}-{end}")
        out.extend(
            f"{number:>5}| {lines[number - 1]}" for number in range(start, end + 1)
        )
    return "\n".join(out) + "\n"
