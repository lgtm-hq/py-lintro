"""Helpers for showing a tool's raw diagnostic text in human-facing output.

Execution diagnostics (a tool's captured stdout and stderr) are shown in the
console and the Markdown report next to, or instead of, parsed issues. Two
properties matter there and nowhere else: the text must not be able to change
the report's own markup, and a pathological diagnostic must not bury the rest
of the run. JSON keeps the full, unmodified text.
"""

from __future__ import annotations

import re

#: Lines kept from the start of a long diagnostic. Lintro's own failure label
#: and the command line come first, so the head says what failed.
DIAGNOSTIC_HEAD_LINES: int = 20

#: Lines kept from the end of a long diagnostic. A Python traceback names the
#: actual error on its last line, so the tail says why.
DIAGNOSTIC_TAIL_LINES: int = 40

_BACKTICK_RUN = re.compile(r"`+")


def bound_diagnostic(text: str) -> str:
    """Shorten a long diagnostic to its head and tail.

    Text of at most ``DIAGNOSTIC_HEAD_LINES + DIAGNOSTIC_TAIL_LINES`` lines is
    returned unchanged. Longer text keeps both ends and names, in place of the
    middle, how many lines were left out and where the full text is.

    Args:
        text: Diagnostic text, already stripped of surrounding whitespace.

    Returns:
        The text, or its head and tail around an omission notice.
    """
    lines = text.splitlines()
    omitted = len(lines) - DIAGNOSTIC_HEAD_LINES - DIAGNOSTIC_TAIL_LINES
    if omitted <= 0:
        return text
    notice = (
        f"... ({omitted} lines omitted by lintro; the full output is in the "
        "JSON report: --output-format json)"
    )
    return "\n".join(
        [
            *lines[:DIAGNOSTIC_HEAD_LINES],
            notice,
            *lines[-DIAGNOSTIC_TAIL_LINES:],
        ],
    )


def markdown_code_block(text: str, *, info: str = "text") -> list[str]:
    """Wrap text in a Markdown fence the text itself cannot close.

    CommonMark closes a backtick fence only with a run of at least as many
    backticks, so a fence one longer than the longest run inside the text keeps
    every line of it literal: an embedded fence cannot end the block early and
    let the rest of the diagnostic render as tables or HTML.

    Args:
        text: Literal text to show.
        info: Info string for the opening fence.

    Returns:
        Lines of the fenced block, followed by a blank line.
    """
    longest = max((len(run) for run in _BACKTICK_RUN.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{info}", text, fence, ""]
