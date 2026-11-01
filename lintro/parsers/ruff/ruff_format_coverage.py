"""Conservative coverage evidence from a completed native Ruff format check."""

import re
from pathlib import Path

_CHANGED = re.compile(r"^(\d+) files? would be reformatted(?:,|$)", re.MULTILINE)
_UNCHANGED = re.compile(
    r"^(?:\d+ files? would be reformatted, )?(\d+) files? already formatted$",
    re.MULTILINE,
)
_FORMATTED = re.compile(
    r"\[ruff::commands::format\]\[DEBUG\] Formatted (\d+) files? in ",
)
_PATH = re.compile(
    r"\[ruff::commands::format\]\[DEBUG\] format_path; path=(.+)$",
    re.MULTILINE,
)


def format_report_parse_failures(output: str, files: list[str]) -> int:
    """Count formatting findings missing from the native summary.

    Args:
        output: Native formatter report.
        files: Parsed formatting findings.

    Returns:
        Number of unaccounted formatting findings.
    """
    summary = _CHANGED.search(output)
    return abs(int(summary[1]) - len(files)) if summary else 0


def format_inspected_paths(output: str) -> frozenset[str] | None:
    """Read exact formatter paths only when its verbose completion agrees.

    These paths come from the formatter's own execution, so format-specific
    exclusions cannot be mistaken for lint coverage. Unknown trace formats
    retain existing acknowledgements.

    Args:
        output: Complete verbose output from a successful native format check.

    Returns:
        Affirmatively inspected files, or None for unknown or incomplete evidence.
    """
    summary = _FORMATTED.search(output)
    changed = _CHANGED.search(output)
    unchanged = _UNCHANGED.search(output)
    if summary is None or (changed is None and unchanged is None):
        return None
    try:
        paths = [Path(match[1]) for match in _PATH.finditer(output)]
        total = (int(changed[1]) if changed else 0) + (
            int(unchanged[1]) if unchanged else 0
        )
        if len(paths) != int(summary[1]) or len(set(paths)) != total:
            return None
        if any(not path.is_absolute() or not path.is_file() for path in paths):
            return None
        return frozenset(str(path.resolve()) for path in paths)
    except (OSError, ValueError, RuntimeError):
        return None
