"""Source-map types for template-aware preprocessing.

Maps rendered host-language line numbers back to original ``*.jinja``
template coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass(frozen=True)
class SourceMap:
    """Line mapping from a rendered file back to its template source.

    Attributes:
        original_path: Absolute path to the original ``*.jinja`` template.
        rendered_path: Absolute path to the stub-rendered host-language file.
        rendered_to_original: Mapping of 1-based rendered line → 1-based
            original template line. Missing keys fall back to the rendered
            line number (best-effort).
    """

    original_path: str
    rendered_path: str
    rendered_to_original: dict[int, int] = field(default_factory=dict)

    def lookup_line(self, rendered_line: int) -> int:
        """Map a rendered line number to the original template line.

        Args:
            rendered_line: 1-based line in the rendered file (0 means unknown).

        Returns:
            1-based original template line, or ``rendered_line`` when unmapped.
        """
        if rendered_line <= 0:
            return rendered_line
        return self.rendered_to_original.get(rendered_line, rendered_line)


def build_source_map(
    original_text: str,
    rendered_text: str,
    *,
    original_path: str,
    rendered_path: str,
) -> SourceMap:
    """Build a best-effort rendered→original line map.

    When line counts match (typical for stub-only substitutions without
    control-flow expansion), mapping is 1:1. Otherwise ``difflib`` opcodes
    align equal regions and map inserted/replaced rendered lines to the
    nearest original line.

    Args:
        original_text: Raw template source.
        rendered_text: Stub-rendered host-language source.
        original_path: Absolute path to the original template.
        rendered_path: Absolute path to the rendered file.

    Returns:
        SourceMap for issue translation.
    """
    original_lines = original_text.splitlines()
    rendered_lines = rendered_text.splitlines()

    if not rendered_lines:
        return SourceMap(
            original_path=original_path,
            rendered_path=rendered_path,
            rendered_to_original={},
        )

    if len(original_lines) == len(rendered_lines):
        equal_map = {index: index for index in range(1, len(rendered_lines) + 1)}
        return SourceMap(
            original_path=original_path,
            rendered_path=rendered_path,
            rendered_to_original=equal_map,
        )

    line_map: dict[int, int] = {}
    matcher = SequenceMatcher(a=original_lines, b=rendered_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for original_idx, rendered_idx in zip(
                range(i1, i2),
                range(j1, j2),
                strict=True,
            ):
                line_map[rendered_idx + 1] = original_idx + 1
            continue

        if tag in {"replace", "insert"}:
            fallback = i1 + 1 if original_lines else 1
            if original_lines:
                fallback = min(max(fallback, 1), len(original_lines))
            for rendered_idx in range(j1, j2):
                line_map[rendered_idx + 1] = fallback

    return SourceMap(
        original_path=original_path,
        rendered_path=rendered_path,
        rendered_to_original=line_map,
    )
