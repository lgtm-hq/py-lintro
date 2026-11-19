"""Read the ``Error during extraction`` lines osv-scanner prints (#3352).

osv-scanner logs one line per plugin that did not succeed::

    Error during extraction: (extracting as <plugin>) <message>

``<message>`` is ``<path>: <error>`` for an extractor that failed on one file
and, for an extractor that failed on several, an empty first line followed by
one tab-indented ``<path>: <error>`` line per file. Otherwise it is the
plugin's failure reason. Paths are printed relative to ``/`` (the root
osv-scanner walks from), with no leading separator. Checked against
osv-scanner 2.4.0 and 2.7.1 and their source (``pkg/osvscanner/scan.go``,
``logPluginStatus``; osv-scalibr ``enricher/transitivedependency``; deps.dev
``util/resolve``).

The reader **fails closed**. Every error is a skipped source, which leaves the
scan incomplete, unless it has one exactly known harmless shape: a
**resolution note**. That is a message of the optional transitive-dependency
enricher (``transitivedependency/requirements`` or
``transitivedependency/pomxml``)::

    failed resolution for <path>: <ending>

where ``<path>`` names one existing regular file and ``<ending>`` is, in full,
one of the registry answers in :data:`HARMLESS_RESOLUTION_ENDINGS`: the
package or version is not on the public registry, or the requirements cannot
be satisfied. Those mean the manifest's directly listed packages were scanned
and only their indirect dependencies were not resolved. Any other ending —
a transport failure (``rpc error``, ``max retries exhausted``, a timeout, a
refused connection, TLS, DNS, an HTTP status) or text never seen before — is a
skipped source, so an outage of the resolution service is never a clean scan.

The enricher also logs each failure as a warning line before the error block.
Every line is given one owner by its position and form alone, never by
comparing its text with another line:

* a header line starts a block; a block continues only with the lines the
  real tool prints under a header (a tab-indented ``<path>: <error>`` line,
  and for a transitive enricher a further ``failed resolution for`` message
  and the cause lines of a ``resolution impossible:`` message);
* a ``failed resolution for`` line outside a block is a warning echo, judged
  on its own by the same allow-list;
* blank lines and the routine messages of
  :mod:`lintro.tools.osv_scanner.routine_lines`, each matched as a whole
  line, carry no error;
* every other line is unrecognised, and an unrecognised line leaves the run
  incomplete at any exit status.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path

from loguru import logger

from lintro.tools.core.incomplete_run import tool_message_excerpt
from lintro.tools.osv_scanner.routine_lines import RoutineLines

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

_NAME = r"[A-Za-z0-9._-]+"
#: deps.dev prints PyPI as ``System(7)`` and Maven as ``Maven``.
_SYSTEM = r"(?:[A-Za-z]+|System\(\d+\))"
_PACKAGE_KEY = rf"{_SYSTEM}:{_NAME}(?::{_NAME})?"
_VERSION = r"[A-Za-z0-9._+~!-]+"
_VERSION_KEY = rf"{_PACKAGE_KEY}\[[A-Za-z]+:{_VERSION}\]"
#: deps.dev's ``ErrNotFound``, returned only for a gRPC ``NotFound`` reply.
_NOT_FOUND = rf"(?:package {_PACKAGE_KEY}|version {_VERSION_KEY}): not found"
_MAVEN_ROOT = rf"\{{Maven:{_NAME}:{_NAME}\[Concrete:{_VERSION}\] \{{\}}\}}"
_CAUSE = (
    rf'(?:no candidates at all for: {_NAME}|requirements conflict: {_NAME}:) "[^"\n]*"'
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


class EntryKind(StrEnum):
    """What an extraction error means for the scan."""

    SKIPPED_SOURCE = auto()
    RESOLUTION = auto()


@dataclass(frozen=True)
class ExtractionEntry:
    """One error osv-scanner reported.

    Attributes:
        kind: Whether a source was skipped or only its resolution failed.
        plugin: The plugin osv-scanner named in parentheses; empty for a
            warning echo, which names none.
        text: The entry as printed, path included.
        lines: Indices of the standard-error lines this entry owns.
        source: Absolute path of the one regular file the entry names, or
            None when it names none or cannot be told apart.
        printed_path: ``source`` as osv-scanner printed it, without the
            leading separator, or None.
    """

    kind: EntryKind
    plugin: str
    text: str
    lines: tuple[int, ...] = ()
    source: str | None = None
    printed_path: str | None = None


@dataclass(frozen=True)
class ExtractionLog:
    """What osv-scanner's standard error says about the extraction.

    Attributes:
        entries: Every error block entry osv-scanner reported, in order.
        echoes: The warning lines the transitive enricher logs before its
            block, each classified on its own.
        unexplained: Indices of lines that are neither routine nor part of
            an entry: the unrecognised lines, any of which may be an error.
        lines: The standard-error lines the indices refer to.
    """

    entries: tuple[ExtractionEntry, ...]
    echoes: tuple[ExtractionEntry, ...]
    unexplained: tuple[int, ...]
    lines: tuple[str, ...] = ()

    @property
    def owned_text(self) -> tuple[str, ...]:
        """Return the lines the error entries own, in order.

        Returns:
            The lines.
        """
        owned = sorted(
            {index for entry in (*self.entries, *self.echoes) for index in entry.lines},
        )
        return tuple(self.lines[index] for index in owned if self.lines[index])

    @property
    def unexplained_text(self) -> tuple[str, ...]:
        """Return the unexplained lines as printed.

        Returns:
            The lines, in order.
        """
        return tuple(self.lines[index] for index in self.unexplained)


@dataclass(frozen=True)
class ExtractionErrors:
    """The extraction errors of one run, sorted by what they mean.

    Attributes:
        skipped: Errors for a source that was not scanned, a failed lookup,
            or a resolution that failed for an unrecognised reason; each
            leaves the scan incomplete.
        notes: Failed transitive resolutions of a manifest whose directly
            listed packages were scanned, for a reason known to be harmless.
        failure_message: osv-scanner's own message for a failed run: the
            lines no extraction error explains, with paths shortened.
        explains_failed_exit: Whether every error line osv-scanner logged
            belongs to a recognised harmless entry.
        unrecognised: The lines that are neither routine nor part of an
            entry, as printed. Any one of them leaves the run incomplete,
            whatever the exit status.
    """

    skipped: tuple[ExtractionEntry, ...]
    notes: tuple[ExtractionEntry, ...]
    failure_message: str
    explains_failed_exit: bool
    unrecognised: tuple[str, ...] = ()

    @property
    def unexplained(self) -> str:
        """Return the unrecognised lines as one quote.

        Returns:
            The lines joined by newlines.
        """
        return "\n".join(self.unrecognised)


def _named_file(path_text: str, scan_root: Path) -> tuple[str, str] | None:
    """Return the regular file a printed path names, if exactly one.

    Args:
        path_text: The path as osv-scanner printed it.
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
    source; when several splits name different files the entry is ambiguous.

    Args:
        text: The entry, starting with the path.
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


def is_harmless_ending(ending: str) -> bool:
    """Return whether a resolution error is a known harmless registry answer.

    Args:
        ending: The message after ``failed resolution for <path>: ``.

    Returns:
        True only when the whole message is one allow-listed shape.
    """
    return any(pattern.fullmatch(ending) for pattern in HARMLESS_RESOLUTION_ENDINGS)


def _entry(
    plugin: str,
    text: str,
    lines: tuple[int, ...],
    scan_root: Path,
    *,
    transitive: bool,
) -> ExtractionEntry:
    """Classify one reported error.

    Args:
        plugin: The plugin osv-scanner named, or empty for a warning echo.
        text: The entry as printed.
        lines: The standard-error lines the entry owns.
        scan_root: Directory osv-scanner ran in.
        transitive: Whether a transitive enricher printed it.

    Returns:
        A resolution note when the enricher, wording, file and the whole
        error after the path all match a harmless shape, else a skipped
        source.
    """
    resolution = transitive and text.startswith(_RESOLUTION_PREFIX)
    named = source_of(
        text.removeprefix(_RESOLUTION_PREFIX) if resolution else text,
        scan_root,
    )
    harmless = resolution and named is not None and is_harmless_ending(named[2])
    return ExtractionEntry(
        kind=EntryKind.RESOLUTION if harmless else EntryKind.SKIPPED_SOURCE,
        plugin=plugin,
        text=text,
        lines=lines,
        source=named[0] if named else None,
        printed_path=named[1] if named else None,
    )


def _read_message(
    lines: list[str],
    start: int,
    first: str,
) -> tuple[str, tuple[int, ...], int]:
    """Read one ``failed resolution for`` message from its first line on.

    Only a ``resolution impossible:`` message runs over several lines: its
    cause lines, then one blank line, follow directly beneath it.

    Args:
        lines: Every standard-error line.
        start: Index of the message's first line.
        first: The message text on that line.

    Returns:
        The message, the indices it owns, and the index after it.
    """
    text = [first]
    owned = [start]
    end = start + 1
    if first.endswith(_IMPOSSIBLE):
        while end < len(lines) and lines[end].startswith(_CAUSE_PREFIXES):
            text.append(lines[end])
            owned.append(end)
            end += 1
        if len(text) > 1 and end < len(lines) and not lines[end]:
            owned.append(end)
            end += 1
    return "\n".join(text), tuple(owned), end


def _read_block(
    lines: list[str],
    start: int,
    match: re.Match[str],
    scan_root: Path,
) -> tuple[list[ExtractionEntry], int]:
    """Read one error block from its header line on.

    Args:
        lines: Every standard-error line.
        start: Index of the header line.
        match: The header match.
        scan_root: Directory osv-scanner ran in.

    Returns:
        One entry per file or per message, and the index after the block.
    """
    plugin = match.group("plugin")
    transitive = plugin in TRANSITIVE_PLUGINS
    detail = match.group("detail").strip()
    messages: list[tuple[str, tuple[int, ...]]] = []
    end = start + 1
    if detail:
        text, owned, end = _read_message(lines, start, detail)
        messages.append((text, owned))
    while end < len(lines):
        line = lines[end]
        if line.startswith("\t"):
            messages.append((line.strip(), (end,)))
            end += 1
        elif transitive and line.startswith(_RESOLUTION_PREFIX):
            text, owned, end = _read_message(lines, end, line)
            messages.append((text, owned))
        else:
            break
    if not messages:
        # A header with nothing after it names no source; never drop it.
        messages = [(f"{plugin} reported an error", (start,))]
    elif start not in messages[0][1]:
        messages[0] = (messages[0][0], (start, *messages[0][1]))
    entries = [
        _entry(plugin, text, owned, scan_root, transitive=transitive)
        for text, owned in messages
    ]
    return entries, end


def read_extraction_log(
    stderr: str | None,
    scan_root: Path,
    routine: RoutineLines | None = None,
) -> ExtractionLog:
    """Read every extraction error from osv-scanner's standard error.

    Each line gets exactly one owner, decided by its position and form; see
    the module docstring.

    Args:
        stderr: What osv-scanner printed on standard error.
        scan_root: Directory osv-scanner ran in.
        routine: The routine lines of this run; by default those of a run
            with no configuration file.

    Returns:
        The reported errors, the warning echoes and the lines nothing
        explains.
    """
    routine = routine or RoutineLines.for_run(scan_root)
    lines = [line.rstrip("\r") for line in (stderr or "").splitlines()]
    entries: list[ExtractionEntry] = []
    echoes: list[ExtractionEntry] = []
    unexplained: list[int] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _HEADER_RE.match(line)
        if match is not None:
            block, index = _read_block(lines, index, match, scan_root)
            entries.extend(block)
        elif line.startswith(_RESOLUTION_PREFIX):
            text, owned, index = _read_message(lines, index, line)
            echoes.append(_entry("", text, owned, scan_root, transitive=True))
        elif not line.strip():
            index += 1
        elif length := routine.length_at(lines, index):
            index += length
        else:
            unexplained.append(index)
            index += 1
    return ExtractionLog(
        entries=tuple(entries),
        echoes=tuple(echoes),
        unexplained=tuple(unexplained),
        lines=tuple(lines),
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


def relative_to_root(text: str, scan_root: Path) -> str:
    """Shorten paths under the scan root inside an osv-scanner message.

    Args:
        text: The message, which may name paths without their leading ``/``.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The message with each scan-root prefix removed.
    """
    for root in {str(scan_root), str(scan_root.resolve())}:
        text = text.replace(root.lstrip(os.sep) + os.sep, "")
    return text


def shown_entry(entry: ExtractionEntry, scan_root: Path) -> str:
    """Return an extraction error with its paths relative to the scan root.

    Args:
        entry: The error osv-scanner reported.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The entry with the named source shortened, so a quote keeps
        osv-scanner's own error instead of a long absolute path.
    """
    text = entry.text.removeprefix(_RESOLUTION_PREFIX)
    if entry.source is None or entry.printed_path is None:
        return relative_to_root(text, scan_root)
    shown = display_path(entry.source, scan_root)
    return text.replace(entry.printed_path, shown)


#: The short form of a resolution note, for a summary table's Notes column.
RESOLUTION_NOTE_SUMMARY: str = "indirect dependencies not resolved"


def resolution_note(notes: tuple[ExtractionEntry, ...], scan_root: Path) -> str:
    """Name the manifests whose indirect dependencies were not resolved.

    Args:
        notes: The resolution failures osv-scanner reported.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The note, or an empty string when there is none.
    """
    if not notes:
        return ""
    shown = "\n".join(shown_entry(entry, scan_root) for entry in notes)
    return (
        "OSV-Scanner scanned the packages listed in these manifests but "
        "could not resolve their indirect dependencies, so those were not "
        "checked: "
        f"{tool_message_excerpt(shown)}"
    )


def skipped_reason(
    skipped: tuple[ExtractionEntry, ...],
    scan_root: Path,
    *,
    unexplained: str = "",
) -> str:
    """Name the sources osv-scanner skipped, for an incomplete run.

    Args:
        skipped: The errors that left part of the scan undone.
        scan_root: Directory osv-scanner ran in.
        unexplained: Other error lines osv-scanner printed, quoted after the
            skipped sources so a failed lookup is never left out.

    Returns:
        The reason, quoting osv-scanner's own errors.
    """
    # A resolution failure is printed twice, as a warning and as the error;
    # quote it once. Which lines each entry owns is unaffected.
    shown = "\n".join(
        dict.fromkeys(
            [
                *(shown_entry(entry, scan_root) for entry in skipped),
                *([relative_to_root(unexplained, scan_root)] if unexplained else []),
            ],
        ),
    )
    return (
        "OSV-Scanner could not extract every package source or look up "
        "its vulnerabilities, so the scan is incomplete: "
        f"{tool_message_excerpt(shown)}"
    )


def unrecognised_reason(unrecognised: str, scan_root: Path) -> str | None:
    """Name the lines that keep an otherwise clean exit from a verdict.

    A line that is neither routine nor part of a recognised entry may be a
    failed lookup or an error lintro has not seen. At any exit status, beside
    findings or not, the scan then cannot be shown complete, so the lines are
    quoted instead of dropped.

    Args:
        unrecognised: The unrecognised lines, joined by newlines.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The reason, or None when every line was recognised.
    """
    if not unrecognised:
        return None
    quoted = tool_message_excerpt(relative_to_root(unrecognised, scan_root))
    return (
        "OSV-Scanner printed a message lintro does not recognise, so the scan "
        f"cannot be shown to be complete: {quoted}"
    )


def sort_extraction_errors(
    stderr: str | None,
    scan_root: Path,
    *,
    is_excluded: Callable[[str], bool],
    routine: RoutineLines | None = None,
) -> ExtractionErrors:
    """Sort the extraction errors osv-scanner reported by what they mean.

    An error naming one regular file the exclusion set covers is dropped, as
    a finding for that file would be (#1725); an error whose file cannot be
    identified is always kept.

    Args:
        stderr: What osv-scanner printed on standard error.
        scan_root: Directory osv-scanner ran in.
        is_excluded: Whether lintro excludes a source path.
        routine: The routine lines of this run.

    Returns:
        The skipped sources, the resolution notes, the message to quote for
        a failed run, the unrecognised lines, and whether the errors explain
        a failed exit: exit 127 is a verdict only when every line that is
        not routine or blank belongs to an entry, and every entry is a
        harmless resolution or lies under an excluded path.
    """
    log = read_extraction_log(stderr, scan_root, routine)
    skipped: list[ExtractionEntry] = []
    notes: list[ExtractionEntry] = []
    for entry in (*log.entries, *log.echoes):
        if entry.source is not None and is_excluded(entry.source):
            logger.debug(
                f"[osv-scanner] Ignoring extraction error for an excluded "
                f"path: {entry.source}",
            )
            continue
        if entry.kind is not EntryKind.RESOLUTION:
            skipped.append(entry)
        elif entry.plugin:
            # A harmless echo owns its own lines; the note comes from the
            # error block, which names the enricher.
            notes.append(entry)
    # Quote what is not routine: the unrecognised lines first, else the
    # error entries, else everything osv-scanner printed.
    quoted = (
        "\n".join(log.unexplained_text)
        or "\n".join(log.owned_text)
        or (stderr or "").strip()
    )
    return ExtractionErrors(
        skipped=tuple(skipped),
        notes=tuple(notes),
        failure_message=relative_to_root(tool_message_excerpt(quoted), scan_root),
        explains_failed_exit=(
            bool(log.entries) and not skipped and not log.unexplained
        ),
        unrecognised=log.unexplained_text,
    )
