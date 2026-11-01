"""Conservative coverage evidence from native Ruff discovery and format reports."""

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


def reported_inspected_paths(
    paths: list[str],
    *,
    files: list[str],
    cwd: str | None,
) -> frozenset[str] | None:
    """Authenticate native paths against unambiguous invocation identities.

    Args:
        paths: Absolute paths reported by Ruff, without trimming or rewriting.
        files: Exact file arguments passed to the native command.
        cwd: Working directory of that command.

    Returns:
        Canonical inspected files, or None for ambiguous or foreign identities.
    """
    try:
        base = Path(cwd or Path.cwd())
        expected: dict[str, str] = {}
        invoked: set[str] = set()
        for file in files:
            path = (base / file).absolute()
            resolved = str(path.resolve())
            # Native deduplication cannot disambiguate repeated file arguments.
            if resolved in invoked:
                return None
            invoked.add(resolved)
            # Validate both spellings before splitting any native report. A
            # symlink can introduce separators absent from its invocation name.
            for name in (str(path), resolved):
                if name.splitlines() != [name] or any(
                    ord(char) < 32 or ord(char) == 127 for char in name
                ):
                    return None
                expected[name] = resolved
        inspected: set[str] = set()
        for name in paths:
            identity = expected.get(name)
            if identity is None or identity in inspected or not Path(name).is_file():
                return None
            inspected.add(identity)
        return frozenset(inspected)
    except (OSError, ValueError, RuntimeError):
        return None


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


def format_inspected_paths(
    output: str,
    *,
    files: list[str],
    cwd: str | None,
) -> frozenset[str] | None:
    """Read exact formatter paths only when its verbose completion agrees.

    These paths come from the formatter's own execution, so format-specific
    exclusions cannot be mistaken for lint coverage. Unknown trace formats
    retain existing acknowledgements.

    Args:
        output: Complete verbose output from a successful native format check.
        files: Exact file arguments passed to Ruff format.
        cwd: Working directory used by Ruff.

    Returns:
        Affirmatively inspected files, or None for unknown or incomplete evidence.
    """
    summary = _FORMATTED.search(output)
    changed = _CHANGED.search(output)
    unchanged = _UNCHANGED.search(output)
    if summary is None or (changed is None and unchanged is None):
        return None
    try:
        paths = [match[1] for match in _PATH.finditer(output)]
        total = (int(changed[1]) if changed else 0) + (
            int(unchanged[1]) if unchanged else 0
        )
        if len(paths) != int(summary[1]) or len(paths) != total:
            return None
        return reported_inspected_paths(paths, files=files, cwd=cwd)
    except (OSError, ValueError, RuntimeError):
        return None
