"""Read what osv-scanner prints on standard error at ``--verbosity error``.

lintro runs osv-scanner with ``--verbosity error``, so a scan that went well
prints nothing on standard error. Anything it does print is an error, and the
run is incomplete, with one exception: a **resolution note**. That is the
optional transitive-dependency enricher reporting that the registry has no
such package or version, or that no version satisfies the requirements, on
one line::

    Error during extraction: (extracting as transitivedependency/requirements)
    failed resolution for <path>: package System(7):<name>: not found

The enricher is ``transitivedependency/requirements`` or
``transitivedependency/pomxml``, ``<path>`` names one existing regular file,
and the whole text after it is one of :data:`HARMLESS_RESOLUTION_ENDINGS`. The
manifest's listed packages were scanned; only their indirect dependencies were
not resolved. A transport failure (``rpc error``, ``max retries exhausted``, a
timeout) or any text not listed is an error.

Standard error is read as messages, not lines. A message starts with an
``Error during extraction`` header and continues on the lines osv-scanner
prints under it: tab-indented ``<path>: <error>`` lines when one extractor
failed on several files, further ``failed resolution for`` messages joined to
a transitive one, and the cause lines of ``resolution impossible:``. That
error ends in a newline of its own, so osv-scanner prints one blank line after
its last cause; after that blank line only a further ``failed resolution for``
continues the message. Each part of a message is judged on its own; a message
with any part that is neither a resolution note nor an error for a file lintro
excludes is kept whole as error text. Every line outside such a message is
error text too.

A part is an error for an excluded file only when it names exactly one path:
one existing regular file under an excluded path at its start, and no other
path anywhere after it (no path separator, and no word that names an existing
path in the scan root). Checked
against osv-scanner 2.4.0 and 2.7.1 (``pkg/osvscanner/scan.go``,
``logPluginStatus``; osv-scalibr ``enricher/transitivedependency``; deps.dev
``util/resolve``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lintro.tools.core.incomplete_run import tool_message_excerpt

#: The first line osv-scanner prints for a plugin that did not succeed.
_HEADER_RE = re.compile(
    r"^Error during extraction: \((?:extracting as )?(?P<plugin>[^)]*)\) ?"
    r"(?P<detail>.*)$",
)

#: Enrichers that resolve indirect dependencies of an already-read manifest.
TRANSITIVE_PLUGINS: frozenset[str] = frozenset(
    {"transitivedependency/requirements", "transitivedependency/pomxml"},
)

#: How each transitive enricher names a manifest it could not resolve.
_RESOLUTION_PREFIX: str = "failed resolution for "

#: The first line of the pip-style resolver's "unsatisfiable" error.
_IMPOSSIBLE: str = "resolution impossible:"

#: How each cause line under ``resolution impossible:`` starts.
_CAUSE_PREFIXES: tuple[str, ...] = (
    "no candidates at all for: ",
    "requirements conflict: ",
)

#: What osv-scanner prints, alone, when it found nothing to scan: the whole
#: line, the same at 2.4.0 and 2.7.1 (``cmd/osv-scanner/internal/cmd/run.go``).
NO_SOURCES_LINE: str = "No package sources found, --help for usage information."

_NAME = r"[A-Za-z0-9._-]+"
#: deps.dev prints PyPI as ``System(7)`` and Maven as ``Maven``.
_SYSTEM = r"(?:[A-Za-z]+|System\(\d+\))"
_PACKAGE_KEY = rf"{_SYSTEM}:{_NAME}(?::{_NAME})?"
_VERSION = r"[A-Za-z0-9._+~!-]+"
_VERSION_KEY = rf"{_PACKAGE_KEY}\[[A-Za-z]+:{_VERSION}\]"
#: deps.dev's ``ErrNotFound``, returned only for a gRPC ``NotFound`` reply.
_NOT_FOUND = rf"(?:package {_PACKAGE_KEY}|version {_VERSION_KEY}): not found"
_MAVEN_ROOT = rf"\{{Maven:{_NAME}:{_NAME}\[Concrete:{_VERSION}\] \{{\}}\}}"
#: One requirement specifier as deps.dev's PyPI resolver prints it: an
#: operator and a version, with the spaces the manifest had kept (2.4.0 prints
#: ``">= 99999 , < 100000"``). ``===`` (arbitrary equality) takes any version
#: text without spaces, commas, quotes or backslashes.
_SPECIFIER = r' *(?:===[^\s",\\]+|(?:~=|==|!=|<=|>=|<|>) *[A-Za-z0-9._+*!-]+) *'
#: ``%q`` of the requirements joined with ``,`` (``requirementsConflictedError``).
_SPECIFIERS = rf'"{_SPECIFIER}(?:,{_SPECIFIER})*"'
_CAUSE = (
    rf"(?:no candidates at all for: {_NAME}|requirements conflict: {_NAME}:) "
    rf"{_SPECIFIERS}"
)

#: The only endings after ``failed resolution for <path>: `` that are
#: harmless, each matched against the whole remaining message:
#:
#: * ``package System(7):<name>: not found`` and
#:   ``version <key>[Concrete:<v>]: not found``: the registry answered that it
#:   has no such package or version (a private package);
#: * ``failed resolving {Maven:<g>:<a>[Concrete:<v>] {}}: <the same>``: the
#:   Maven enricher wrapping that answer;
#: * ``resolution impossible:`` followed by one or more
#:   ``no candidates at all for: <name> "<spec>"`` or
#:   ``requirements conflict: <name>: "<specs>"`` lines: the registry answered
#:   and no version satisfies the requirements. deps.dev raises this only from
#:   versions it received; a lookup that failed in transport is returned as
#:   its own error instead.
HARMLESS_RESOLUTION_ENDINGS: tuple[re.Pattern[str], ...] = (
    re.compile(_NOT_FOUND),
    re.compile(rf"failed resolving {_MAVEN_ROOT}: {_NOT_FOUND}"),
    re.compile(rf"{re.escape(_IMPOSSIBLE)}(?:\n{_CAUSE})+"),
)

#: The short form of a resolution note, for a summary table's Notes column.
RESOLUTION_NOTE_SUMMARY: str = "indirect dependencies not resolved"


@dataclass(frozen=True)
class ResolutionNote:
    """One manifest whose indirect dependencies were not resolved.

    Attributes:
        source: Absolute path of the manifest.
        printed_path: The path as osv-scanner printed it.
        text: The message after ``failed resolution for ``, path included.
    """

    source: str
    printed_path: str
    text: str


@dataclass(frozen=True)
class StderrReport:
    """What osv-scanner's standard error says about one run.

    Attributes:
        notes: The resolution notes, outside excluded paths.
        errors: Every other line osv-scanner printed, verbatim and in order,
            except blank lines and messages wholly about excluded files.
        excluded: How many messages were dropped because every file they
            name is excluded.
        printed: Every non-blank line osv-scanner printed, verbatim, for
            quoting when the run is incomplete.
    """

    notes: tuple[ResolutionNote, ...] = ()
    errors: tuple[str, ...] = ()
    excluded: int = 0
    printed: tuple[str, ...] = ()

    @property
    def explained(self) -> bool:
        """Return whether osv-scanner logged only notes or excluded errors.

        Returns:
            True when it logged something and all of it is accounted for.
        """
        return not self.errors and bool(self.notes or self.excluded)

    @property
    def only_no_sources(self) -> bool:
        """Return whether the one error is "No package sources found".

        Returns:
            True when that line is all osv-scanner printed besides notes and
            excluded errors.
        """
        return self.errors == (NO_SOURCES_LINE,)


def _named_file(path_text: str, scan_root: Path) -> tuple[str, str] | None:
    """Return the regular file a printed path names, if exactly one.

    Args:
        path_text: The path as osv-scanner printed it, usually without its
            leading separator.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The absolute path and the printed form, or None.
    """
    if not path_text.strip():
        return None
    files = {
        str(path)
        for path in (Path(os.sep + path_text.lstrip(os.sep)), scan_root / path_text)
        if os.path.isfile(path)
    }
    if len({os.path.realpath(path) for path in files}) != 1:
        return None
    absolute = os.sep + path_text.lstrip(os.sep)
    return (absolute if absolute in files else files.pop()), path_text


def source_of(text: str, scan_root: Path) -> tuple[str, str, str] | None:
    """Return the one regular file at the start of ``<path>: <error>``.

    The path may itself contain ``": "``, so every split point on the first
    line is tried. A split that names a directory, or nothing, is not a
    source; when several splits name different files the part is ambiguous.

    Args:
        text: The part, starting with the path.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The absolute path, the printed form and the error after the path, or
        None when no split, or more than one, names an existing regular file.
    """
    first_line = text.split("\n", 1)[0]
    found: dict[str, tuple[str, str, str]] = {}
    start = 0
    while (index := first_line.find(": ", start)) >= 0:
        named = _named_file(first_line[:index], scan_root)
        if named is not None:
            found[os.path.realpath(named[0])] = (*named, text[index + 2 :])
        start = index + 1
    if len(found) != 1:
        return None
    return next(iter(found.values()))


#: Characters stripped from a word before it is looked up as a path.
_WORD_PUNCTUATION: str = "\"'`()[]{}<>,;:"


def names_another_path(error: str, scan_root: Path) -> bool:
    """Return whether the error after a file's path names any further path.

    An error for an excluded file is dropped only when the excluded file is
    the one path in it; ``<excluded>: failed reading <included>: ...`` is
    about the included file as well. Any path separator counts as a path, as
    does any word that names an existing file or directory in the scan root.

    Args:
        error: The text after the file's path, every line of it.
        scan_root: Directory osv-scanner ran in.

    Returns:
        True when the text may name another path.
    """
    if "/" in error or "\\" in error or os.sep in error:
        return True
    for word in error.split():
        name = word.strip(_WORD_PUNCTUATION)
        if name in ("", ".", "..") or set(name) == {"."}:
            continue
        if os.path.lexists(scan_root / name):
            return True
    return False


def is_harmless_ending(ending: str) -> bool:
    """Return whether a resolution error is a known harmless registry answer.

    Args:
        ending: The message after ``failed resolution for <path>: ``.

    Returns:
        True only when the whole message is one allow-listed shape.
    """
    return any(pattern.fullmatch(ending) for pattern in HARMLESS_RESOLUTION_ENDINGS)


def _read_message(
    lines: list[str],
    start: int,
    match: re.Match[str],
) -> tuple[list[str], int]:
    """Read one ``Error during extraction`` message from its header on.

    Args:
        lines: Every standard-error line.
        start: Index of the header line.
        match: The header match.

    Returns:
        The message's parts (the header's own text, if any, then one per
        continuation), and the index after the message.
    """
    transitive = match.group("plugin") in TRANSITIVE_PLUGINS
    detail = match.group("detail").strip()
    parts = [detail] if detail else []
    # True once the blank line that ends a ``resolution impossible:`` block
    # has been read: only a further ``failed resolution for`` may follow.
    closed = False
    end = start + 1
    while end < len(lines):
        line = lines[end]
        block = bool(parts) and _IMPOSSIBLE in parts[-1].split("\n", 1)[0]
        if closed:
            if not (transitive and line.startswith(_RESOLUTION_PREFIX)):
                break
            parts.append(line)
            closed = False
        elif block and line.startswith(_CAUSE_PREFIXES):
            parts[-1] = f"{parts[-1]}\n{line}"
        elif block and "\n" in parts[-1] and not line.strip():
            closed = True
        elif line.startswith("\t"):
            parts.append(line.strip())
        elif transitive and line.startswith(_RESOLUTION_PREFIX):
            parts.append(line)
        else:
            break
        end += 1
    return parts, end


def read_stderr(
    stderr: str | None,
    scan_root: Path,
    *,
    is_excluded: Callable[[str], bool],
) -> StderrReport:
    """Sort what osv-scanner printed into notes and error text.

    Args:
        stderr: What osv-scanner printed on standard error.
        scan_root: Directory osv-scanner ran in.
        is_excluded: Whether lintro excludes a source path.

    Returns:
        The resolution notes, the error lines, and the number of messages
        dropped because they concern only excluded files.
    """
    lines = [line.rstrip("\r") for line in (stderr or "").splitlines()]
    notes: list[ResolutionNote] = []
    errors: list[str] = []
    excluded = 0
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        match = _HEADER_RE.match(lines[index])
        if match is None:
            errors.append(lines[index])
            index += 1
            continue
        transitive = match.group("plugin") in TRANSITIVE_PLUGINS
        parts, end = _read_message(lines, index, match)
        message_notes: list[ResolutionNote] = []
        all_excluded = bool(parts)
        harmless = bool(parts)
        for part in parts:
            resolution = transitive and part.startswith(_RESOLUTION_PREFIX)
            text = part.removeprefix(_RESOLUTION_PREFIX) if resolution else part
            named = source_of(text, scan_root)
            if (
                named is not None
                and is_excluded(named[0])
                and not names_another_path(named[2], scan_root)
            ):
                continue
            all_excluded = False
            if resolution and named is not None and is_harmless_ending(named[2]):
                message_notes.append(ResolutionNote(named[0], named[1], text))
            else:
                harmless = False
        if all_excluded:
            excluded += 1
        elif harmless:
            notes.extend(message_notes)
        else:
            errors.extend(line for line in lines[index:end] if line.strip())
        index = end
    return StderrReport(
        notes=tuple(notes),
        errors=tuple(errors),
        excluded=excluded,
        printed=tuple(line for line in lines if line.strip()),
    )


def display_path(source: str, scan_root: Path) -> str:
    """Return a source path relative to the scan root when it lies inside it.

    Args:
        source: Absolute path of the source.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The relative path, or ``source`` when it lies outside the scan root.
    """
    try:
        return str(Path(source).resolve().relative_to(scan_root.resolve()))
    except ValueError:
        return source


def resolution_note(notes: tuple[ResolutionNote, ...], scan_root: Path) -> str:
    """Name the manifests whose indirect dependencies were not resolved.

    Args:
        notes: The resolution notes osv-scanner's errors carried.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The note, or an empty string when there is none.
    """
    if not notes:
        return ""
    shown = "\n".join(
        note.text.replace(note.printed_path, display_path(note.source, scan_root), 1)
        for note in notes
    )
    return (
        "OSV-Scanner scanned the packages listed in these manifests but "
        "could not resolve their indirect dependencies, so those were not "
        "checked: "
        f"{tool_message_excerpt(shown)}"
    )
