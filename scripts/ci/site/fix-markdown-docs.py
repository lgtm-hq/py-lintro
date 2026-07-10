#!/usr/bin/env python3
"""Fix markdownlint issues in Astro docs content."""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINE_LENGTH = 100
DOCS_ROOT = (
    Path(__file__).resolve().parents[3] / "apps" / "site" / "src" / "content" / "docs"
)
MARKDOWN_LINK = re.compile(
    r"(\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\[[^\]]*\])",
)
PROSE_TOKEN = re.compile(r"\w+|[^\s\w]")
_NO_SPACE_BEFORE = frozenset(",.:;?!)]}")

FENCE_LANG_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/"), "http"),
    (re.compile(r"^\{"), "json"),
    (re.compile(r"^\["), "json"),
    (re.compile(r"^curl\s"), "bash"),
    (re.compile(r"^(export |RUST_LOG=|SENTRY_|DATABASE_|WORKOS_|PADDLE_)"), "bash"),
    (re.compile(r"^scrape_configs:"), "yaml"),
    (re.compile(r"^test:"), "yaml"),
    (re.compile(r"^rustume "), "bash"),
    (re.compile(r"^cargo "), "bash"),
    (re.compile(r"^make "), "bash"),
    (re.compile(r"^docker "), "bash"),
    (re.compile(r"^uv run "), "bash"),
    (re.compile(r"^Validation errors:"), "text"),
    (re.compile(r"^┌"), "text"),
]


def infer_fence_language(block_lines: list[str]) -> str:
    """Infer a fenced-code language tag from block contents."""
    first_line = next((line for line in block_lines if line.strip()), "")
    for pattern, language in FENCE_LANG_HINTS:
        if pattern.search(first_line):
            return language
    return "text"


def wrap_line(line: str, width: int = LINE_LENGTH) -> list[str]:
    """Wrap prose to width, treating markdown links as atomic tokens.

    Args:
        line: Single prose line without block-level markdown syntax.
        width: Maximum characters per output line.

    Returns:
        One or more wrapped lines not exceeding width when possible.
    """
    if len(line) <= width:
        return [line]

    parts = MARKDOWN_LINK.split(line)
    tokens: list[str] = []
    for index, part in enumerate(parts):
        if not part:
            continue
        if index % 2 == 1:
            tokens.append(part)
            continue
        tokens.extend(PROSE_TOKEN.findall(part))

    if not tokens:
        return [line]

    wrapped: list[str] = []
    current = ""
    for token in tokens:
        if current and token in _NO_SPACE_BEFORE:
            candidate = f"{current}{token}"
        elif current:
            candidate = f"{current} {token}"
        else:
            candidate = token
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            wrapped.append(current)
        if len(token) > width:
            wrapped.append(token)
            current = ""
            continue
        current = token
    if current:
        wrapped.append(current)
    return wrapped or [line]


def fix_frontmatter_description(frontmatter: str) -> str:
    """Wrap long YAML description values using folded style."""
    match = re.search(
        r"^(description:\s*)([\"']?)(.+?)\2\s*$",
        frontmatter,
        flags=re.MULTILINE,
    )
    if not match:
        return frontmatter

    prefix, _quote, value = match.groups()
    if len(f"{prefix}{value}") <= LINE_LENGTH:
        return frontmatter

    folded = "\n".join(
        [
            f"{prefix}>",
            *[f"  {line}" for line in wrap_line(value.strip(), LINE_LENGTH - 2)],
        ],
    )
    return frontmatter[: match.start()] + folded + frontmatter[match.end() :]


def normalize_fence_lines(lines: list[str]) -> list[str]:
    """Repair fences and add missing language tags."""
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```") and not stripped.startswith("````"):
            language = stripped[3:].strip()

            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                candidate = lines[index].strip()
                if candidate in {"```", "```text"}:
                    break
                block_lines.append(lines[index])
                index += 1

            if not language:
                language = infer_fence_language(block_lines)

            output.append(f"```{language}")
            output.extend(block_lines)
            output.append("```")
            index += 1
            continue

        output.append(line)
        index += 1

    return output


def fix_heading_levels(lines: list[str]) -> list[str]:
    """Keep heading increments to one level at a time.

    Fenced code blocks are skipped so language comments like ``# note`` are
    not treated as markdown headings (which would also corrupt
    ``previous_level`` for subsequent real headings).
    """
    output: list[str] = []
    previous_level = 0
    in_code = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") and not stripped.startswith("````"):
            in_code = not in_code
            output.append(line)
            continue

        if in_code:
            output.append(line)
            continue

        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            output.append(line)
            continue

        level = len(match.group(1))
        title = match.group(2).rstrip(":").strip()
        if previous_level and level > previous_level + 1:
            level = previous_level + 1

        previous_level = level
        output.append(f"{'#' * level} {title}")

    return output


def ensure_blank_lines_around_fences(lines: list[str]) -> list[str]:
    """Ensure fenced code blocks have surrounding blank lines."""
    refined: list[str] = []
    in_fence = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        is_fence = stripped.startswith("```") and not stripped.startswith("````")

        if is_fence and not in_fence:
            if refined and refined[-1].strip():
                refined.append("")
            refined.append(line)
            in_fence = True
            continue

        if is_fence and in_fence:
            refined.append(line)
            in_fence = False
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            if next_line.strip():
                refined.append("")
            continue

        refined.append(line)

    return refined


def is_standalone_bold_line(stripped: str) -> bool:
    """Return whether a line is standalone **bold**, not HR or bold-italic.

    Args:
        stripped: Trimmed markdown line.

    Returns:
        True for ``**title**``; false for ``****``, ``***x***``, or empty bold.
    """
    if (
        len(stripped) < 4
        or not stripped.startswith("**")
        or not stripped.endswith("**")
    ):
        return False
    inner = stripped[2:-2]
    return bool(inner.strip()) and "*" not in inner


def fix_emphasis_headings(lines: list[str]) -> list[str]:
    """Convert standalone bold lines into level-4 headings."""
    fixed: list[str] = []
    in_code = False

    for index, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("```") and not stripped.startswith("````"):
            in_code = not in_code
            fixed.append(line)
            continue

        if in_code:
            fixed.append(line)
            continue

        prev = lines[index - 1].strip() if index > 0 else ""
        if is_standalone_bold_line(stripped) and (
            index == 0
            or not (prev.startswith(("- ", "* ", "+ ")) or re.match(r"^\d+\.", prev))
        ):
            title = stripped[2:-2].strip()
            fixed.append(f"#### {title}")
            continue
        fixed.append(line)
    return fixed


def wrap_prose_lines(lines: list[str]) -> list[str]:
    """Wrap long prose lines outside structural markdown blocks."""
    output: list[str] = []
    in_code = False
    in_table = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```") and not stripped.startswith("````"):
            in_code = not in_code
            output.append(line)
            continue

        if in_code:
            output.append(line)
            continue

        if stripped.startswith("|"):
            in_table = True
            output.append(line)
            continue

        if in_table and not stripped:
            in_table = False

        if in_table or not stripped or stripped.startswith("#"):
            output.append(line)
            continue

        if stripped.startswith(("- ", "* ", "> ")) or re.match(r"^\d+\.", stripped):
            output.append(line)
            continue

        if len(line) <= LINE_LENGTH:
            output.append(line)
            continue

        indent = line[: len(line) - len(line.lstrip())]
        wrap_width = max(20, LINE_LENGTH - len(indent))
        output.extend(
            f"{indent}{wrapped}" for wrapped in wrap_line(line.strip(), wrap_width)
        )

    return output


def fix_markdown(source: str) -> str:
    """Apply markdownlint-oriented fixes to a docs page."""
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", source)
    if not match:
        return source

    frontmatter = fix_frontmatter_description(match.group(1))
    body_lines = source[match.end() :].splitlines()

    body_lines = normalize_fence_lines(body_lines)
    body_lines = fix_emphasis_headings(body_lines)
    body_lines = ensure_blank_lines_around_fences(body_lines)
    body_lines = fix_heading_levels(body_lines)
    body_lines = wrap_prose_lines(body_lines)

    body = "\n".join(body_lines).rstrip() + "\n"
    return f"---\n{frontmatter}\n---\n{body}"


def main() -> int:
    """Fix all markdown files under the docs content root."""
    if not DOCS_ROOT.is_dir():
        print(f"Docs root not found: {DOCS_ROOT}", file=sys.stderr)
        return 1

    for path in sorted(DOCS_ROOT.rglob("*.md")):
        original = path.read_text(encoding="utf-8")
        updated = fix_markdown(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            print(f"fixed {path.relative_to(DOCS_ROOT)}")

    return 0


if __name__ == "__main__":
    import argparse

    argparse.ArgumentParser(description=__doc__).parse_args()
    raise SystemExit(main())
