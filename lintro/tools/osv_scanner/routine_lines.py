"""The routine lines osv-scanner prints on standard error (#3352).

Every line osv-scanner prints is either one of the routine messages listed
here, part of an extraction error block, or **unrecognised**. An unrecognised
line makes the run incomplete whatever the exit status, so a message lintro
has never seen (a failed lookup, a new error, or a routine line a future
osv-scanner version words differently) is quoted to the user and never read
as a clean scan.

Each routine message is matched against the **whole line**, anchored at both
ends; nothing is accepted by prefix. The patterns were collected from real
runs of osv-scanner 2.4.0 and 2.7.1 (the projects and cases are listed in
``docs/guides/baseline.md``) and checked against the format strings in their
source:

* ``Scanning dir <dir>`` where ``<dir>`` is the directory lintro scanned;
* ``Starting filesystem walk for root: /``;
* ``Scanned <path> file and found <n> package`` (``packages`` when ``n`` is
  not 1), where ``<path>`` exists (osv-scanner prints this line for a
  directory too, for example a ``parsers`` directory with 0 packages);
* ``End status: <n> dirs visited, <n> inodes visited, <n> Extract calls,
  <duration> elapsed, <duration> wall time``;
* ``Filtered <n> vulnerability from output`` (``vulnerabilities`` when ``n``
  is not 1), ``Filtered <n> local/unscannable package/s from the scan.`` and
  ``Filtered <n> ignored package/s from the scan.``;
* ``Warning: enricher transitivedependency/pomxml may be risky when run on
  untrusted artifacts. Please ensure you trust the source code and
  artifacts.``;
* ``No package sources found, --help for usage information.``;
* the messages about the project's ``.osv-scanner.toml``, each checked
  against what that file actually says: ``<ID> has been filtered out
  because: <reason>`` (and the ``and 1 alias have been`` and ``and <n>
  aliases have been`` forms), ``Package <ecosystem>/<name>/<version> has
  been filtered out because: <reason>``, ``<config> has unused ignores:``
  and ``<config> has unused overrides:`` with their `` - <entry>`` lines,
  ``warning: <config> has multiple ignores for <ID> - only the first will be
  used!`` and ``warning: <config> has at least one ignore entry using
  'ignoreUntil' which has been deprecated in favor of 'effectiveUntil'``. A
  reason is printed as written, so a reason spanning several lines is
  matched line by line.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

#: How osv-scanner prints a reason left empty in the configuration.
NO_REASON: str = "(no reason given)"

_COUNT = r"(?P<count>\d+)"
#: A Go ``time.Duration`` as ``%s`` prints it, such as ``1m2.5s`` or ``175µs``.
_DURATION = r"(?:\d+(?:\.\d+)?(?:h|m|s|ms|µs|μs|us|ns))+"

#: Routine lines that name nothing lintro must check, matched whole.
_FIXED_LINES: tuple[re.Pattern[str], ...] = (
    re.compile(r"Starting filesystem walk for root: /"),
    re.compile(
        rf"End status: \d+ dirs visited, \d+ inodes visited, \d+ Extract calls, "
        rf"{_DURATION} elapsed, {_DURATION} wall time",
    ),
    re.compile(r"Filtered \d+ local/unscannable package/s from the scan\."),
    re.compile(r"Filtered \d+ ignored package/s from the scan\."),
    re.compile(
        r"Warning: enricher transitivedependency/pomxml may be risky when run on "
        r"untrusted artifacts\. Please ensure you trust the source code and "
        r"artifacts\.",
    ),
    re.compile(r"No package sources found, --help for usage information\."),
)

_SCANNED = re.compile(
    rf"Scanned (?P<path>.+) file and found {_COUNT} (?P<noun>packages?)",
)
_FILTERED_VULNS = re.compile(
    rf"Filtered {_COUNT} (?P<noun>vulnerability|vulnerabilities) from output",
)
_SCANNING_DIR = re.compile(r"Scanning dir (?P<dir>.+)")
_ALIASES = r"(?: has been| and 1 alias have been| and \d+ aliases have been)"


def _plural_agrees(match: re.Match[str], singular: str) -> bool:
    """Return whether a count and its noun agree as osv-scanner prints them.

    Args:
        match: A match with ``count`` and ``noun`` groups.
        singular: The noun's singular form.

    Returns:
        True when the noun is singular exactly when the count is 1.
    """
    return (match.group("noun") == singular) == (match.group("count") == "1")


@dataclass(frozen=True)
class _Message:
    """One routine message that may span several lines.

    Attributes:
        first: Pattern the first line must match whole.
        rest: The following lines, each matched exactly.
    """

    first: re.Pattern[str]
    rest: tuple[str, ...] = ()

    def length_at(self, lines: Sequence[str], index: int) -> int:
        """Return how many lines the message covers at ``index``.

        Args:
            lines: Every standard-error line.
            index: Where the message would start.

        Returns:
            The number of lines, or 0 when the message is not there.
        """
        if not self.first.fullmatch(lines[index]):
            return 0
        end = index + 1 + len(self.rest)
        if tuple(lines[index + 1 : end]) != self.rest:
            return 0
        return 1 + len(self.rest)


def _text_lines(text: str) -> list[str]:
    """Split printed text into lines, dropping trailing blank ones.

    Args:
        text: The text.

    Returns:
        Its lines.
    """
    lines = text.split("\n")
    while len(lines) > 1 and not lines[-1].strip():
        lines.pop()
    return lines


def _message(prefix: str, text: str) -> _Message:
    """Build a message whose last part is free text printed as written.

    Args:
        prefix: Regular expression for the first line before the text.
        text: The text, possibly over several lines.

    Returns:
        The message.
    """
    lines = _text_lines(text)
    return _Message(
        first=re.compile(prefix + re.escape(lines[0])),
        rest=tuple(lines[1:]),
    )


def _describe_override(entry: dict[str, Any]) -> str:
    """Describe a package override the way osv-scanner 2.7.1 lists it.

    Args:
        entry: One ``[[PackageOverrides]]`` table.

    Returns:
        The description.
    """
    parts = [
        f"{label}: {entry[key]}"
        for label, key in (
            ("Ecosystem", "ecosystem"),
            ("Name", "name"),
            ("Version", "version"),
            ("Group", "group"),
            ("Reason", "reason"),
        )
        if isinstance(entry.get(key), str) and entry[key]
    ]
    return ", ".join(parts) or "(blanket ignore)"


def _tables(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Return the tables of one array in a parsed configuration.

    Args:
        data: The parsed configuration.
        key: The array's name.

    Returns:
        The tables, skipping anything that is not one.
    """
    value = data.get(key, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _reason(entry: dict[str, Any]) -> str:
    """Return a configured reason as osv-scanner prints it.

    Args:
        entry: One configuration table.

    Returns:
        The reason, or the placeholder osv-scanner prints for none.
    """
    reason = entry.get("reason")
    return reason if isinstance(reason, str) and reason else NO_REASON


def _config_messages(config: Path) -> list[_Message]:
    """Build the routine messages a configuration file can cause.

    Args:
        config: The ``.osv-scanner.toml`` passed to osv-scanner.

    Returns:
        One message per line the file can make osv-scanner print.
    """
    try:
        with config.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.debug(f"[osv-scanner] Cannot read {config}: {error}")
        return []
    names = "|".join(re.escape(name) for name in {str(config), str(config.resolve())})
    path = f"(?:{names})"
    messages = [
        _Message(re.compile(f"{path} has unused ignores:")),
        _Message(re.compile(f"{path} has unused overrides:")),
        _Message(
            re.compile(
                f"warning: {path} has at least one ignore entry using "
                "'ignoreUntil' which has been deprecated in favor of "
                "'effectiveUntil'",
            ),
        ),
    ]
    for entry in _tables(data, "IgnoredVulns"):
        vuln_id = entry.get("id")
        if not isinstance(vuln_id, str) or not vuln_id:
            continue
        quoted = re.escape(vuln_id)
        messages += [
            _message(f"{quoted}{_ALIASES} filtered out because: ", _reason(entry)),
            _Message(re.compile(f" - {quoted}")),
            _Message(
                re.compile(
                    f"warning: {path} has multiple ignores for {quoted} - only "
                    "the first will be used!",
                ),
            ),
        ]
    for entry in _tables(data, "PackageOverrides"):
        if entry.get("ignore") is True:
            messages.append(
                _message(
                    # A scoped npm name holds a slash; the reason anchors it.
                    r"Package \S+/\S.* has been filtered out because: ",
                    _reason(entry),
                ),
            )
        messages.append(_message(" - ", _describe_override(entry)))
    return messages


@dataclass(frozen=True)
class RoutineLines:
    """Recognises the routine lines of one osv-scanner run.

    Attributes:
        scan_root: Directory osv-scanner scanned.
        messages: Routine messages that need no further check.
    """

    scan_root: Path
    messages: tuple[_Message, ...]

    @classmethod
    def for_run(cls, scan_root: Path, config: Path | None = None) -> RoutineLines:
        """Build the matcher for one run.

        Args:
            scan_root: Directory osv-scanner scanned.
            config: The ``.osv-scanner.toml`` passed with ``--config``, if any.

        Returns:
            The matcher.
        """
        messages = [_Message(pattern) for pattern in _FIXED_LINES]
        if config is not None:
            messages += _config_messages(config)
        return cls(scan_root=scan_root, messages=tuple(messages))

    def _checked_line(self, line: str) -> bool:
        """Return whether a line is a routine line whose values check out.

        Args:
            line: One standard-error line.

        Returns:
            True for a scanned-directory, scanned-file or filtered-count line
            whose directory, file or count agrees with the run.
        """
        checks: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], bool]], ...] = (
            (
                _SCANNING_DIR,
                lambda m: m.group("dir")
                in {".", str(self.scan_root), str(self.scan_root.resolve())},
            ),
            (
                _SCANNED,
                lambda m: os.path.exists(m.group("path"))
                and _plural_agrees(m, "package"),
            ),
            (_FILTERED_VULNS, lambda m: _plural_agrees(m, "vulnerability")),
        )
        for pattern, check in checks:
            match = pattern.fullmatch(line)
            if match is not None and check(match):
                return True
        return False

    def length_at(self, lines: Sequence[str], index: int) -> int:
        """Return how many lines a routine message covers at ``index``.

        Args:
            lines: Every standard-error line.
            index: Where the message would start.

        Returns:
            The number of lines of the longest routine message there, or 0
            when the line is not routine.
        """
        if self._checked_line(lines[index]):
            return 1
        return max(
            (message.length_at(lines, index) for message in self.messages),
            default=0,
        )
