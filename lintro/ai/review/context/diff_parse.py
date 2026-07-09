"""Unified diff and git metadata parsing for review context."""

from __future__ import annotations

import re

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.changed_file import ChangedFile

_DIFF_FILE_HEADER = re.compile(
    r'^diff --git (?:"a/((?:[^"\\]|\\.)+)"|a/(.+?)) (?:"b/((?:[^"\\]|\\.)+)"|b/(.+?))$',
    re.MULTILINE,
)
_MINUS_MINUS_HEADER = re.compile(
    r'^--- (?:"a/((?:[^"\\]|\\.)+)"|a/(.+?))(?:\t|$)',
    re.MULTILINE,
)
_PLUS_PLUS_HEADER = re.compile(
    r'^\+\+\+ (?:"b/((?:[^"\\]|\\.)+)"|b/(.+?))(?:\t|$)',
    re.MULTILINE,
)
_NAME_STATUS_LINE = re.compile(
    r"^(?P<status>[A-Z][A-Z0-9]*)\t(?P<path>[^\t]+)(?:\t(?P<new_path>[^\t]+))?$",
)
_NUMSTAT_RENAME_LINE = re.compile(r"^(\d+|-)\t(\d+|-)\t?$")

_BRACE_COMPRESSED_RENAME = re.compile(r"\{([^=>{}]+)=>([^=>{}]+)\}")

_BACKSLASH_PLACEHOLDER = "\uffff"


def _decode_git_octal_escapes(*, text: str) -> str:
    """Decode git C-style octal byte escapes into a UTF-8 path."""
    byte_parts = bytearray()
    index = 0
    while index < len(text):
        if (
            text[index] == "\\"
            and index + 1 < len(text)
            and "0" <= text[index + 1] <= "7"
        ):
            end = index + 1
            while end < len(text) and end < index + 4 and "0" <= text[end] <= "7":
                end += 1
            value = int(text[index + 1 : end], 8)
            if value > 0xFF:
                byte_parts.extend(
                    text[index].encode("utf-8", errors="surrogateescape"),
                )
                index += 1
                continue
            byte_parts.append(value)
            index = end
            continue
        byte_parts.extend(text[index].encode("utf-8", errors="surrogateescape"))
        index += 1
    return byte_parts.decode("utf-8", errors="surrogateescape")


def _unquote_git_path(*, path: str) -> str:
    """Unescape a git-quoted path from a diff header."""
    if not path:
        return path
    placeholder = _BACKSLASH_PLACEHOLDER
    interim = path.replace("\\\\", placeholder)
    interim = (
        interim.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\r", "\r")
        .replace("\\a", "\a")
        .replace("\\b", "\b")
        .replace("\\f", "\f")
        .replace("\\v", "\v")
    )
    interim = _decode_git_octal_escapes(text=interim)
    return interim.replace(placeholder, "\\")


def _numstat_path_keys(*, path: str) -> tuple[str, ...]:
    """Return lookup keys for a ``git diff --numstat`` path field."""
    keys: list[str] = [path]
    if " => " in path:
        old_path, new_path = (part.strip() for part in path.split(" => ", 1))
        keys.extend([new_path, path, old_path])
    brace_match = _BRACE_COMPRESSED_RENAME.search(path)
    if brace_match is not None:
        old_part = brace_match.group(1).strip()
        new_part = brace_match.group(2).strip()
        prefix = path[: brace_match.start()]
        suffix = path[brace_match.end() :]
        expanded_old = f"{prefix}{old_part}{suffix}"
        expanded_new = f"{prefix}{new_part}{suffix}"
        keys.extend([expanded_new, expanded_old, new_part, old_part, path])
    return tuple(dict.fromkeys(keys))


def parse_changed_files(*, name_status: str, numstat: str) -> list[ChangedFile]:
    """Parse ``git diff --name-status -z`` and ``--numstat -z`` output.

    Args:
        name_status: Raw NUL-delimited output from ``git diff --name-status -z``.
        numstat: Raw NUL-delimited output from ``git diff --numstat -z``.

    Returns:
        Parsed changed file entries.

    Raises:
        ReviewContextError: When git diff metadata lines cannot be parsed.
    """
    stats: dict[str, tuple[int, int]] = {}
    unparsed_numstat: list[str] = []

    for record in _iter_numstat_records(raw=numstat):
        if record is None:
            unparsed_numstat.append("invalid numstat record")
            continue
        additions, deletions, path = record
        for stat_path in _numstat_path_keys(path=path):
            stats[stat_path] = (additions, deletions)

    changed_files: list[ChangedFile] = []
    unparsed_name_status: list[str] = []

    for entry in _iter_name_status_records(raw=name_status):
        if entry is None:
            unparsed_name_status.append("invalid name-status record")
            continue
        status_code, path, new_path = entry
        normalized_status = _normalize_status(status_code=status_code)
        previous_path = (
            path
            if normalized_status
            in {ChangedFileStatus.RENAMED, ChangedFileStatus.COPIED}
            and new_path
            else None
        )
        file_path = (
            new_path
            if normalized_status
            in {ChangedFileStatus.RENAMED, ChangedFileStatus.COPIED}
            and new_path
            else path
        )
        additions, deletions = stats.get(file_path, stats.get(path, (0, 0)))
        changed_files.append(
            ChangedFile(
                path=file_path,
                status=normalized_status,
                additions=additions,
                deletions=deletions,
                previous_path=previous_path,
            ),
        )

    unparsed = [*unparsed_numstat, *unparsed_name_status]
    if unparsed:
        raise ReviewContextError(
            "Failed to parse git diff metadata: " f"{'; '.join(unparsed[:3])}",
            code=ReviewContextErrorCode.GIT_OUTPUT_PARSE_FAILED,
        )

    return changed_files


def _iter_numstat_records(
    *,
    raw: str,
) -> list[tuple[int, int, str] | None]:
    """Parse NUL-delimited ``git diff --numstat -z`` records."""
    if not raw:
        return []

    if "\0" not in raw:
        return _iter_numstat_records_legacy(raw=raw.rstrip("\0"))

    records: list[tuple[int, int, str] | None] = []
    parts = raw.split("\0")
    index = 0
    while index < len(parts):
        token = parts[index]
        index += 1
        if not token:
            continue
        rename_match = _NUMSTAT_RENAME_LINE.match(token)
        if rename_match is not None:
            if index + 1 >= len(parts):
                records.append(None)
                continue
            new_path = parts[index + 1]
            index += 2
            if not new_path:
                records.append(None)
                continue
            additions_raw, deletions_raw = rename_match.groups()
            additions = 0 if additions_raw == "-" else int(additions_raw)
            deletions = 0 if deletions_raw == "-" else int(deletions_raw)
            records.append((additions, deletions, new_path))
            continue
        parsed = _parse_numstat_counts_and_path(token=token)
        if parsed is None:
            records.append(None)
            continue
        records.append(parsed)
    return records


def _parse_numstat_counts_and_path(*, token: str) -> tuple[int, int, str] | None:
    """Parse additions, deletions, and path from one numstat field."""
    if token.count("\t") < 2:
        return None
    additions_raw, deletions_raw, path = token.split("\t", 2)
    try:
        additions = 0 if additions_raw == "-" else int(additions_raw)
        deletions = 0 if deletions_raw == "-" else int(deletions_raw)
    except ValueError:
        return None
    return additions, deletions, path


def _iter_numstat_records_legacy(*, raw: str) -> list[tuple[int, int, str] | None]:
    """Parse legacy line-based numstat output for tests."""
    records: list[tuple[int, int, str] | None] = []
    for line in raw.splitlines():
        trimmed = line.rstrip("\n")
        if not trimmed:
            continue
        parsed = _parse_numstat_counts_and_path(token=trimmed)
        if parsed is None:
            records.append(None)
            continue
        records.append(parsed)
    return records


def _iter_name_status_records(
    *,
    raw: str,
) -> list[tuple[str, str, str | None] | None]:
    """Parse NUL-delimited ``git diff --name-status -z`` records."""
    if not raw:
        return []

    if "\0" not in raw:
        return _iter_name_status_records_legacy(raw=raw)

    tokens = [token for token in raw.split("\0") if token]
    records: list[tuple[str, str, str | None] | None] = []
    index = 0
    while index < len(tokens):
        status_code = tokens[index]
        index += 1
        if index >= len(tokens):
            records.append(None)
            break
        path = tokens[index]
        index += 1
        if status_code.startswith(("R", "C")):
            if index >= len(tokens) or not tokens[index]:
                records.append(None)
                continue
            new_path = tokens[index]
            index += 1
            records.append((status_code, path, new_path))
            continue
        records.append((status_code, path, None))
    return records


def _iter_name_status_records_legacy(
    *,
    raw: str,
) -> list[tuple[str, str, str | None] | None]:
    """Parse legacy tab-delimited name-status output for tests."""
    records: list[tuple[str, str, str | None] | None] = []
    for line in raw.splitlines():
        trimmed = line.rstrip("\n")
        if not trimmed:
            continue
        match = _NAME_STATUS_LINE.match(trimmed)
        if match is None:
            records.append(None)
            continue
        status_code = match.group("status")
        new_path = match.group("new_path")
        if status_code.startswith(("R", "C")) and not new_path:
            records.append(None)
            continue
        records.append(
            (
                status_code,
                match.group("path"),
                new_path,
            ),
        )
    return records


def _path_from_diff_section(*, section: str, fallback_path: str) -> str:
    """Return the new path for a diff section, preferring ``+++`` metadata."""
    match = _PLUS_PLUS_HEADER.search(section)
    if match is not None:
        quoted, plain = match.groups()
        raw_path = quoted if quoted is not None else (plain or "")
        if raw_path != "/dev/null":
            return _unquote_git_path(path=raw_path)
    deleted_path = _deleted_path_from_diff_section(section=section)
    if deleted_path is not None:
        return deleted_path
    return fallback_path


def _deleted_path_from_diff_section(*, section: str) -> str | None:
    """Return the deleted file path from ``--- a/`` metadata when present."""
    match = _MINUS_MINUS_HEADER.search(section)
    if match is None:
        return None
    quoted, plain = match.groups()
    raw_path = quoted if quoted is not None else (plain or "")
    if raw_path == "/dev/null":
        return None
    return _unquote_git_path(path=raw_path)


def split_unified_diff_by_file(*, unified_diff: str) -> dict[str, str]:
    """Split a unified diff into per-file diff sections.

    Args:
        unified_diff: Full unified diff text.

    Returns:
        Mapping of repository-relative file path to that file's diff section.
    """
    if not unified_diff.strip():
        return {}

    matches = list(_DIFF_FILE_HEADER.finditer(unified_diff))
    if not matches:
        return {}

    per_file: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(unified_diff)
        )
        section = unified_diff[start:end]
        _old_quoted, _old_plain, new_quoted, new_plain = match.groups()
        header_path = _unquote_git_path(
            path=new_quoted if new_quoted is not None else (new_plain or ""),
        )
        new_path = _path_from_diff_section(section=section, fallback_path=header_path)
        if new_path in per_file:
            per_file[new_path] += section
        else:
            per_file[new_path] = section

    return per_file


def unified_diff_preamble(*, unified_diff: str) -> str:
    """Return bytes before the first ``diff --git`` header, if any."""
    match = _DIFF_FILE_HEADER.search(unified_diff)
    if match is None:
        return unified_diff if unified_diff.strip() else ""
    return unified_diff[: match.start()]


def _count_diff_hunk_changes(*, diff_text: str) -> tuple[int, int]:
    """Count added and removed lines inside unified diff hunks."""
    additions = 0
    deletions = 0
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith(("diff --git", "--- ", "+++ ")):
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _previous_path_from_diff_section(*, diff_text: str) -> str | None:
    """Return the source path for rename/copy diff sections."""
    header = diff_text.split("\n@@", 1)[0]
    rename_from = re.search(r"^rename from (.+)$", header, re.MULTILINE)
    if rename_from is not None:
        raw_path = rename_from.group(1)
        if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] == '"':
            raw_path = raw_path[1:-1]
        return _unquote_git_path(path=raw_path)
    copy_from = re.search(r"^copy from (.+)$", header, re.MULTILINE)
    if copy_from is not None:
        raw_path = copy_from.group(1)
        if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] == '"':
            raw_path = raw_path[1:-1]
        return _unquote_git_path(path=raw_path)
    return None


def _is_regular_file_mode(*, mode: str) -> bool:
    """Return True when a git mode string denotes a regular file object."""
    return mode.startswith("100")


def _is_mode_only_permission_change(*, header: str) -> bool:
    """Return True when a diff header only changes regular-file permissions."""
    old_match = re.search(r"^old mode (\d+)", header, re.MULTILINE)
    new_match = re.search(r"^new mode (\d+)", header, re.MULTILINE)
    if old_match is None or new_match is None:
        return False
    old_mode = old_match.group(1)
    new_mode = new_match.group(1)
    return _is_regular_file_mode(mode=old_mode) and _is_regular_file_mode(
        mode=new_mode,
    )


def _infer_status_from_diff_section(*, diff_text: str) -> ChangedFileStatus:
    """Infer changed-file status from a unified diff section header."""
    header = diff_text.split("\n@@", 1)[0]
    if re.search(r"^new file mode\b", header, re.MULTILINE):
        return ChangedFileStatus.ADDED
    if re.search(r"^deleted file mode\b", header, re.MULTILINE):
        return ChangedFileStatus.DELETED
    if re.search(r"^rename from\b", header, re.MULTILINE) and re.search(
        r"^rename to\b",
        header,
        re.MULTILINE,
    ):
        return ChangedFileStatus.RENAMED
    if re.search(r"^copy from\b", header, re.MULTILINE) and re.search(
        r"^copy to\b",
        header,
        re.MULTILINE,
    ):
        return ChangedFileStatus.COPIED
    if re.search(r"^old mode\b", header, re.MULTILINE) and re.search(
        r"^new mode\b",
        header,
        re.MULTILINE,
    ):
        if _is_mode_only_permission_change(header=header):
            return ChangedFileStatus.MODIFIED
        return ChangedFileStatus.TYPE_CHANGED
    return ChangedFileStatus.MODIFIED


def _normalize_status(*, status_code: str) -> ChangedFileStatus:
    """Map git name-status codes to normalized review statuses.

    Args:
        status_code: Raw git status token (for example ``R100``).

    Returns:
        Normalized status label.
    """
    if status_code.startswith("A"):
        return ChangedFileStatus.ADDED
    if status_code.startswith("D"):
        return ChangedFileStatus.DELETED
    if status_code.startswith("R"):
        return ChangedFileStatus.RENAMED
    if status_code.startswith("C"):
        return ChangedFileStatus.COPIED
    if status_code.startswith("T"):
        return ChangedFileStatus.TYPE_CHANGED
    return ChangedFileStatus.MODIFIED
