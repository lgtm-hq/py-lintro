"""Read the ``Error during extraction`` lines osv-scanner prints (#3352).

osv-scanner logs one line per plugin that did not succeed::

    Error during extraction: (extracting as <plugin>) <message>

``<message>`` is ``<path>: <error>`` for an extractor that failed on one file
and, for an extractor that failed on several, an empty first line followed by
one tab-indented ``<path>: <error>`` line per file. Otherwise it is the
plugin's failure reason, which may run over several lines. Paths are printed
relative to ``/`` (the root osv-scanner walks from), with no leading
separator. Checked against osv-scanner 2.4.0 and 2.7.1 and their source
(``pkg/osvscanner/scan.go``, ``logPluginStatus``).

Two kinds of entry are told apart:

* a **skipped source**: an extractor could not read or parse a lockfile or
  manifest (``python/requirements``, ``javascript/packagelockjson``,
  ``go/gomod``, ``java/pomxml``, ``rust/cargolock``, ...), or a lookup
  plugin such as ``vulnmatch/osvdev`` failed. That part of the scan did not
  happen.
* a **resolution note**: the optional transitive-dependency enricher
  (``transitivedependency/requirements`` or ``transitivedependency/pomxml``)
  could not resolve the indirect dependencies of a manifest it had already
  read, for example because a package is not on the public registry. Its
  message is ``failed resolution for <path>: <error>``, several of them
  joined by newlines. The manifest's directly listed packages were scanned.

An entry is a resolution note only when its plugin, its wording and an
existing regular file all say so; anything else is a skipped source.
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

#: Lines osv-scanner prints on every run; they report no error.
PROGRESS_PREFIXES: tuple[str, ...] = (
    "Scanning dir ",
    "Starting filesystem walk",
    "End status:",
    "Scanned ",
)

#: Informational lines that do not make osv-scanner exit with an error.
_INFO_PREFIXES: tuple[str, ...] = (
    *PROGRESS_PREFIXES,
    "Filtered ",
    "Warning: enricher transitivedependency/",
)


class EntryKind(StrEnum):
    """What an extraction error means for the scan."""

    SKIPPED_SOURCE = auto()
    RESOLUTION = auto()


@dataclass(frozen=True)
class ExtractionEntry:
    """One error osv-scanner reported for a plugin.

    Attributes:
        kind: Whether a source was skipped or only its resolution failed.
        plugin: The plugin osv-scanner named in parentheses.
        text: The entry as printed, path included.
        source: Absolute path of the one regular file the entry names, or
            None when it names none or cannot be told apart.
        printed_path: ``source`` as osv-scanner printed it, without the
            leading separator, or None.
    """

    kind: EntryKind
    plugin: str
    text: str
    source: str | None = None
    printed_path: str | None = None


@dataclass(frozen=True)
class ExtractionLog:
    """What osv-scanner's standard error says about the extraction.

    Attributes:
        entries: Every error osv-scanner reported, in order.
        unexplained: Lines that are neither progress, information nor part
            of an extraction error; one of them may be what made osv-scanner
            exit with an error.
    """

    entries: tuple[ExtractionEntry, ...]
    unexplained: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionErrors:
    """The extraction errors of one run, sorted by what they mean.

    Attributes:
        skipped: Errors for a source that was not scanned, or a failed
            lookup; each leaves the scan incomplete.
        notes: Failed transitive resolutions of a manifest whose directly
            listed packages were scanned.
        failure_message: osv-scanner's own message for a failed run: the
            lines no extraction error explains, with paths shortened.
        explains_failed_exit: Whether these errors, none of which skipped a
            source, account for everything osv-scanner logged.
    """

    skipped: tuple[ExtractionEntry, ...]
    notes: tuple[ExtractionEntry, ...]
    failure_message: str
    explains_failed_exit: bool


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


def source_of(text: str, scan_root: Path) -> tuple[str, str] | None:
    """Return the one regular file at the start of ``<path>: <error>``.

    The path may itself contain ``": "``, so every split point on the first
    line is tried. A split that names a directory, or nothing, is not a
    source; when several splits name different files the entry is ambiguous.

    Args:
        text: The entry, starting with the path.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The absolute path and the printed form, or None when no split, or
        more than one, names an existing regular file.
    """
    first_line = text.split("\n", 1)[0]
    found: dict[str, tuple[str, str]] = {}
    start = 0
    while (index := first_line.find(": ", start)) >= 0:
        named = _named_file(first_line[:index], scan_root)
        if named is not None:
            found[os.path.realpath(named[0])] = named
        start = index + 1
    if len(found) != 1:
        return None
    return next(iter(found.values()))


def _entry(plugin: str, text: str, scan_root: Path) -> ExtractionEntry:
    """Classify one reported error.

    Args:
        plugin: The plugin osv-scanner named.
        text: The entry as printed.
        scan_root: Directory osv-scanner ran in.

    Returns:
        A resolution note when the plugin, wording and file all agree, else a
        skipped source.
    """
    if plugin in TRANSITIVE_PLUGINS and text.startswith(_RESOLUTION_PREFIX):
        named = source_of(text.removeprefix(_RESOLUTION_PREFIX), scan_root)
        if named is not None:
            return ExtractionEntry(
                kind=EntryKind.RESOLUTION,
                plugin=plugin,
                text=text,
                source=named[0],
                printed_path=named[1],
            )
        return ExtractionEntry(kind=EntryKind.SKIPPED_SOURCE, plugin=plugin, text=text)
    named = source_of(text, scan_root)
    return ExtractionEntry(
        kind=EntryKind.SKIPPED_SOURCE,
        plugin=plugin,
        text=text,
        source=named[0] if named else None,
        printed_path=named[1] if named else None,
    )


def _is_continuation(line: str, *, plugin: str, earlier: set[str]) -> bool:
    """Return whether a line continues the error block above it.

    A tab-indented line is one file of a multi-file error. A transitive
    enricher's message may run over several lines; it also logs each failure
    as a warning before the block, so only lines already printed are taken.

    Args:
        line: The line after the block so far.
        plugin: The block's plugin.
        earlier: Lines printed before the block's header.

    Returns:
        True when the line belongs to the block.
    """
    if _HEADER_RE.match(line):
        return False
    if line.startswith("\t"):
        return True
    if plugin not in TRANSITIVE_PLUGINS or line.startswith(_INFO_PREFIXES):
        return False
    return line.startswith(_RESOLUTION_PREFIX) or (
        bool(line.strip()) and line in earlier
    )


def _block_entries(
    plugin: str,
    detail: str,
    continuation: list[str],
    scan_root: Path,
) -> list[ExtractionEntry]:
    """Split one error block into its entries.

    Args:
        plugin: The block's plugin.
        detail: The text after the plugin on the header line.
        continuation: The lines that belong to the block.
        scan_root: Directory osv-scanner ran in.

    Returns:
        One entry per file or per resolution failure.
    """
    texts: list[str] = [detail] if detail.strip() else []
    for line in continuation:
        if line.startswith("\t"):
            texts.append(line.strip())
        elif line.startswith(_RESOLUTION_PREFIX) or not texts:
            texts.append(line)
        else:
            texts[-1] = f"{texts[-1]}\n{line}"
    if not texts:
        # A header with nothing after it names no source; never drop it.
        texts = [f"{plugin} reported an error"]
    return [_entry(plugin, text.rstrip(), scan_root) for text in texts]


def read_extraction_log(stderr: str | None, scan_root: Path) -> ExtractionLog:
    """Read every extraction error from osv-scanner's standard error.

    Args:
        stderr: What osv-scanner printed on standard error.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The reported errors and the lines nothing explains.
    """
    lines = [line.rstrip("\r") for line in (stderr or "").splitlines()]
    entries: list[ExtractionEntry] = []
    in_block: set[int] = set()
    index = 0
    while index < len(lines):
        match = _HEADER_RE.match(lines[index].strip())
        if match is None:
            index += 1
            continue
        plugin = match.group("plugin")
        earlier = set(lines[:index])
        end = index + 1
        while end < len(lines) and _is_continuation(
            lines[end],
            plugin=plugin,
            earlier=earlier,
        ):
            end += 1
        entries.extend(
            _block_entries(
                plugin,
                match.group("detail").strip(),
                lines[index + 1 : end],
                scan_root,
            ),
        )
        in_block.update(range(index, end))
        index = end
    noted = {
        line
        for entry in entries
        if entry.kind is EntryKind.RESOLUTION
        for line in entry.text.splitlines()
    }
    unexplained = tuple(
        line
        for number, line in enumerate(lines)
        if number not in in_block
        and line.strip()
        and not line.strip().startswith(_INFO_PREFIXES)
        # Each transitive failure is also logged as a warning before its block.
        and line not in noted
    )
    return ExtractionLog(entries=tuple(entries), unexplained=unexplained)


def diagnostic_lines(text: str | None) -> str:
    """Drop osv-scanner's progress lines so a quote starts at the error.

    Args:
        text: What osv-scanner printed on standard error.

    Returns:
        The remaining lines, or ``text`` unchanged when nothing else is left.
    """
    lines = (text or "").splitlines()
    kept = [line for line in lines if not line.strip().startswith(PROGRESS_PREFIXES)]
    return "\n".join(kept) if any(line.strip() for line in kept) else (text or "")


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


def resolution_note(notes: tuple[ExtractionEntry, ...], scan_root: Path) -> str:
    """Name the manifests whose indirect dependencies were not resolved.

    Args:
        notes: The resolution failures osv-scanner reported.
        scan_root: Directory osv-scanner ran in.

    Returns:
        A note for the output, or an empty string when there is none.
    """
    if not notes:
        return ""
    shown = "\n".join(shown_entry(entry, scan_root) for entry in notes)
    return (
        "Note: OSV-Scanner scanned the packages listed in these manifests but "
        "could not resolve their indirect dependencies, so those were not "
        "checked: "
        f"{tool_message_excerpt(shown)}"
    )


def skipped_reason(skipped: tuple[ExtractionEntry, ...], scan_root: Path) -> str:
    """Name the sources osv-scanner skipped, for an incomplete run.

    Args:
        skipped: The errors that left part of the scan undone.
        scan_root: Directory osv-scanner ran in.

    Returns:
        The reason, quoting osv-scanner's own errors.
    """
    shown = "\n".join(shown_entry(entry, scan_root) for entry in skipped)
    return (
        "OSV-Scanner could not extract every package source or look up "
        "its vulnerabilities, so the scan is incomplete: "
        f"{tool_message_excerpt(shown)}"
    )


def sort_extraction_errors(
    stderr: str | None,
    scan_root: Path,
    *,
    is_excluded: Callable[[str], bool],
) -> ExtractionErrors:
    """Sort the extraction errors osv-scanner reported by what they mean.

    An error naming one regular file the exclusion set covers is dropped, as
    a finding for that file would be (#1725); an error whose file cannot be
    identified is always kept.

    Args:
        stderr: What osv-scanner printed on standard error.
        scan_root: Directory osv-scanner ran in.
        is_excluded: Whether lintro excludes a source path.

    Returns:
        The skipped sources, the resolution notes, the message to quote for
        a failed run, and whether the errors explain a failed exit.
    """
    log = read_extraction_log(stderr, scan_root)
    skipped: list[ExtractionEntry] = []
    notes: list[ExtractionEntry] = []
    for entry in log.entries:
        if entry.source is not None and is_excluded(entry.source):
            logger.debug(
                f"[osv-scanner] Ignoring extraction error for an excluded "
                f"path: {entry.source}",
            )
            continue
        (notes if entry.kind is EntryKind.RESOLUTION else skipped).append(entry)
    quoted = "\n".join(log.unexplained) or diagnostic_lines(stderr)
    return ExtractionErrors(
        skipped=tuple(skipped),
        notes=tuple(notes),
        failure_message=relative_to_root(tool_message_excerpt(quoted), scan_root),
        explains_failed_exit=bool(log.entries) and not skipped and not log.unexplained,
    )
