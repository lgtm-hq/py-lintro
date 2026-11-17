"""Quote a line of tool output safely into a user-facing reason (#3498).

It lives in :mod:`lintro.utils` rather than :mod:`lintro.parsers` so that
modules in the ``utils`` layer, such as the advisory runner, can use it
without importing upward (#3497).
"""

from __future__ import annotations

import re

__all__ = ["quote_first_line"]

#: Any terminal escape sequence, for text that is quoted back to the user
#: rather than parsed. In order: control strings (OSC window titles and
#: hyperlinks, DCS, SOS, PM, APC), whose payload runs to BEL or ST and is cut
#: at a newline or the next ESC when unterminated; CSI sequences (colours,
#: cursor moves, line erases); every other ESC sequence, intermediates
#: included; and the 8-bit C1 forms of CSI and the control strings.
_ANSI_CONTROL_PATTERN: re.Pattern[str] = re.compile(
    r"(?:\x1b[\]PX^_]|[\x90\x98\x9d\x9e\x9f])[^\x07\x1b\x9c\n]*(?:\x07|\x1b\\|\x9c)?"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
    r"|\x1b[ -/]*[0-~]",
)


def quote_first_line(text: str | None, *, limit: int) -> str:
    """Return the first non-empty line of tool output, safe to quote.

    Tool output that explains a failure is quoted into reasons that reach the
    terminal and JSON reports. ANSI sequences and other non-printable
    characters are removed, whitespace controls become spaces, and the line
    is cut to ``limit`` characters with an ellipsis.

    Args:
        text: Raw tool output, or None.
        limit: Longest quote returned, ellipsis included; at least 1.

    Returns:
        The cleaned line, or an empty string when the output has none.
    """
    for raw in _ANSI_CONTROL_PATTERN.sub("", text or "").splitlines():
        line = "".join(
            char if char.isprintable() else " " if char.isspace() else ""
            for char in raw
        ).strip()
        if line:
            break
    else:
        return ""
    if len(line) > limit:
        line = line[: max(limit - 1, 0)].rstrip() + "…"
    return line
