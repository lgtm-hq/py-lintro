"""Parser for Stylelint JSON output.

Handles the ``--formatter json`` output produced by stylelint. The formatter
emits a JSON array with one object per linted source file, each carrying a
``warnings`` list (rule violations and ``CssSyntaxError`` parse failures) plus
``parseErrors`` and ``invalidOptionWarnings`` metadata arrays.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro.parsers.stylelint.stylelint_issue import StylelintIssue


def _looks_like_stylelint_payload(data: Any) -> bool:
    """Return True when ``data`` matches Stylelint's per-file result array.

    Args:
        data: A decoded JSON value.

    Returns:
        True if ``data`` contains Stylelint file results. Invalid siblings are
        left for the parser to count, preserving usable findings.
    """
    if not isinstance(data, list):
        return False
    stylelint_keys = {"source", "warnings", "parseErrors", "invalidOptionWarnings"}
    return any(
        isinstance(item, dict) and stylelint_keys.intersection(item) for item in data
    )


def _starts_json_container(output: str, index: int) -> bool:
    """Distinguish JSON openers from unmatched delimiters in log text.

    Args:
        output: Mixed formatter output and log text.
        index: Position of an opening array or object delimiter.

    Returns:
        Whether the next token can begin JSON. A bare opener on its own line
        remains a container, so truncated and nested reports fail closed.
    """
    next_index = index + 1
    while next_index < len(output) and output[next_index] in " \t\r":
        next_index += 1
    if next_index == len(output) or output[next_index] == "\n":
        line_start = output.rfind("\n", 0, index) + 1
        return not output[line_start:index].strip()
    first = output[next_index]
    if output[index] == "{":
        return first in '"}'
    return first in '[{]"0123456789-' or any(
        output.startswith(literal, next_index) for literal in ("true", "false", "null")
    )


def _standalone_scalar_spans(output: str) -> dict[int, int]:
    """Locate JSON scalar lines without repeatedly decoding overlapping text.

    Args:
        output: Mixed formatter output and log text.

    Returns:
        Start and end offsets of scalar lines, including any inside containers.
    """
    decoder = json.JSONDecoder()
    spans = {}
    offset = 0
    for line in output.splitlines(keepends=True):
        value = line.strip()
        if value and value[0] not in "[{":
            try:
                decoder.decode(value)
            except (json.JSONDecodeError, ValueError):
                pass
            else:
                start = offset + len(line) - len(line.lstrip())
                spans[start] = offset + len(line)
        offset += len(line)
    return spans


@dataclass(frozen=True)
class _Span:
    """One outer JSON candidate found in mixed formatter and log output.

    Attributes:
        start: Offset of the opener or scalar.
        end: Offset just past the value, or where an unfinished one stopped.
        complete: Whether the container closed.
        embedded: Whether log text shares its line, before the opener or
            after a single-line value.
    """

    start: int
    end: int
    complete: bool
    embedded: bool


def _text_after(output: str, end: int) -> bool:
    """Report whether a single-line value is followed by text on its line.

    Args:
        output: Mixed formatter output and log text.
        end: Offset just past the value.

    Returns:
        True when the next character other than a space or tab is neither a
        line end nor the end of the output. Only that blank run is read, so
        many values on one long line stay linear.
    """
    index = end
    while index < len(output) and output[index] in " \t\r":
        index += 1
    return index < len(output) and output[index] != "\n"


@dataclass
class _Scanner:
    """Delimiter state for one pass over mixed formatter and log output.

    Attributes:
        output: Mixed formatter output and log text.
        depth: Nesting depth of the current outer container.
        start: Offset of the current outer container's opener.
        in_string: Whether the scan is inside a string in a container.
        escaped: Whether the previous string character was a backslash.
        line_text: Whether the current line has text outside containers.
        opened_mid_line: Whether the current container opened after text.
    """

    output: str
    depth: int = 0
    start: int = 0
    in_string: bool = False
    escaped: bool = False
    line_text: bool = False
    opened_mid_line: bool = False

    def _string_char(self, char: str) -> None:
        """Advance string state by one character.

        Args:
            char: The character inside a string.
        """
        if self.escaped:
            self.escaped = False
        elif char == "\\":
            self.escaped = True
        elif char == '"':
            self.in_string = False

    def _line_end(self, index: int) -> _Span | None:
        """Close the line, dropping a mid-line container that is still open.

        Args:
            index: Offset of the newline.

        Returns:
            The abandoned container, if one was open.
        """
        self.line_text = False
        if not (self.depth and self.opened_mid_line):
            return None
        self.depth = 0
        self.in_string = self.escaped = False
        return _Span(start=self.start, end=index, complete=False, embedded=True)

    def _close(self, index: int) -> _Span | None:
        """Close one level, yielding the outer container when it ends.

        Args:
            index: Offset of the closing delimiter.

        Returns:
            The finished outer container, if this closed it.
        """
        self.depth -= 1
        if self.depth:
            return None
        end = index + 1
        single_line = "\n" not in self.output[self.start : end]
        embedded = self.opened_mid_line or (
            single_line and _text_after(self.output, end)
        )
        self.line_text = True
        return _Span(start=self.start, end=end, complete=True, embedded=embedded)

    def step(self, index: int) -> _Span | None:
        """Advance over one character.

        Args:
            index: Offset of the character.

        Returns:
            A finished or abandoned outer container, if this ends one.
        """
        char = self.output[index]
        if char == "\n":
            return self._line_end(index)
        if self.in_string:
            self._string_char(char)
        elif char == '"' and self.depth:
            self.in_string = True
        elif char in "[{" and (
            self.depth or _starts_json_container(self.output, index)
        ):
            if not self.depth:
                self.start = index
                self.opened_mid_line = self.line_text
            self.depth += 1
        elif char in "]}" and self.depth:
            return self._close(index)
        elif not self.depth and char not in " \t\r":
            self.line_text = True
        return None


def _outer_json_spans(output: str) -> Iterator[_Span]:
    """Locate outer containers and standalone scalars in bounded linear passes.

    A container opened after log text on the same line must close on that
    line: Stylelint prints its report on one line, so an opener inside a log
    message (a quoted ``"["``, a ``[1/3]`` counter) that is still open at the
    line end is log text, and is dropped there instead of swallowing the
    report on the next line (#3352).

    Args:
        output: Mixed formatter output and log text.

    Yields:
        _Span: Outer JSON values, unfinished containers and abandoned
        mid-line openers.
    """
    scalar_spans = _standalone_scalar_spans(output)
    scanner = _Scanner(output)
    index = 0
    while index < len(output):
        if not scanner.depth and index in scalar_spans:
            end = scalar_spans[index]
            yield _Span(start=index, end=end, complete=True, embedded=False)
            # Delimiters inside a scalar string cannot start another report.
            index = end
            scanner.line_text = False
            continue
        span = scanner.step(index)
        if span is not None:
            yield span
        index += 1
    if scanner.depth:
        yield _Span(
            start=scanner.start,
            end=len(output),
            complete=False,
            embedded=scanner.opened_mid_line,
        )


def _looks_like_report(text: str) -> bool:
    """Report whether log-embedded JSON text could be a Stylelint report.

    Args:
        text: An embedded span that did not decode or did not close.

    Returns:
        True when it holds an object with a key, as every report record does,
        so a truncated or malformed report still fails closed.
    """
    return '{"' in text.replace(" ", "")


def _unusable_span_failures(span: _Span, text: str) -> int:
    """Count an outer span that did not close or did not decode.

    Args:
        span: The span.
        text: Its text.

    Returns:
        0 for JSON-like log text embedded in a line, otherwise 1.
    """
    return int(not span.embedded or _looks_like_report(text))


def _is_log_array(data: Any) -> bool:
    """Report whether decoded JSON is a string or typed-warning log array.

    Args:
        data: A decoded outer value that is not a Stylelint report.

    Returns:
        True for a non-empty array of strings or ``{"type": "warning"}``.
    """
    return (
        isinstance(data, list)
        and bool(data)
        and all(
            isinstance(item, str)
            or (isinstance(item, dict) and item.get("type") == "warning")
            for item in data
        )
    )


def _classify(data: Any, span: _Span, empty_starts: set[int]) -> str:
    """Classify a decoded outer value.

    Args:
        data: The decoded value.
        span: Where it was found.
        empty_starts: Offsets of ``[]`` lines that stand alone.

    Returns:
        ``"report"``, ``"empty"``, ``"log"`` (string or typed warning
        arrays), ``"ignore"`` or ``"invalid"``.
    """
    if _looks_like_stylelint_payload(data):
        return "report"
    if span.embedded:
        # Valid JSON inside a log line, e.g. "Config: {...}", is logging.
        return "ignore"
    if data == [] and span.start in empty_starts:
        # An empty report must occupy its own line. Logging like
        # "Plugins: []" is not a report, and must not hide a later one.
        return "empty"
    if _is_log_array(data):
        # String and typed warning arrays can be logging, but cannot validate
        # an empty fallback. Malformed source arrays still count as failures.
        return "log"
    return "ignore" if data == [] else "invalid"


def _extract_json_array(output: str) -> tuple[list[Any] | None, int]:
    """Extract usable Stylelint records and count invalid outer siblings.

    Stylelint writes its JSON payload to stderr, which lintro combines with
    stdout. Skip non-Stylelint JSON arrays (e.g. ``["warning"]`` noise) and
    bracketed log lines so the real payload is selected. Only decode balanced
    outer containers and standalone scalar lines, avoiding repeated scans of
    nested or truncated JSON. Scalar reports count as invalid outer siblings.
    JSON embedded in a log line (a ``[2026-11-12T10:00:00Z]`` timestamp, a
    ``[1/3]`` counter, a logged config object) is log text unless it looks
    like a report record (#3352).

    Args:
        output: The combined stdout/stderr from stylelint.

    Returns:
        Decoded source records (or None without a report) and extraction
        failures. All outer spans are scanned, including after usable reports.
    """
    decoder = json.JSONDecoder()
    # Find standalone [] lines once, rather than rescanning a long log line
    # for every empty array it contains.
    empty_starts = set()
    offset = 0
    for line in output.splitlines(keepends=True):
        if line.strip() == "[]":
            empty_starts.add(offset + line.index("["))
        offset += len(line)
    reports: list[Any] = []
    kinds: set[str] = set()
    failures = 0
    for span in _outer_json_spans(output):
        text = output[span.start : span.end]
        if not span.complete:
            failures += _unusable_span_failures(span, text)
            if span.embedded:
                continue
            break
        try:
            data = decoder.decode(text)
        except (json.JSONDecodeError, ValueError):
            failures += _unusable_span_failures(span, text)
            continue
        kind = _classify(data, span, empty_starts)
        kinds.add(kind)
        if kind == "report":
            reports.extend(data)
        elif kind == "invalid":
            failures += 1
    failures += int("log" in kinds and not reports)
    return (reports if reports or "empty" in kinds else None), failures


def parse_stylelint_output(output: str | None) -> list[StylelintIssue]:
    """Return Stylelint findings through the legacy list API.

    Args:
        output: Raw JSON formatter output, possibly surrounded by log text.

    Returns:
        All usable findings, omitting parser failure accounting.
    """
    issues, _failures = parse_stylelint_output_with_stats(output)
    return issues


def parse_stylelint_output_with_stats(
    output: str | None,
) -> tuple[list[StylelintIssue], int]:
    """Parse Stylelint JSON output into a list of StylelintIssue objects.

    Args:
        output: The raw combined output from stylelint ``--formatter json``.

    Returns:
        Usable findings and the count of invalid report containers or records.
    """
    issues: list[StylelintIssue] = []

    if not output or not output.strip():
        return issues, 0

    failures = 0
    try:
        data: Any = json.loads(output)
    except json.JSONDecodeError:
        data, failures = _extract_json_array(output)
        if data is None:
            return issues, max(1, failures)

    if not isinstance(data, list):
        logger.debug("Stylelint output is not a JSON array")
        return issues, 1

    for source_result in data:
        if not isinstance(source_result, dict):
            failures += 1
            continue
        source = str(source_result.get("source", "") or "")
        source_issues, source_failures = _parse_source_result(source_result, source)
        issues.extend(source_issues)
        failures += source_failures

    return issues, failures


def _parse_source_result(
    source_result: dict[str, Any],
    source: str,
) -> tuple[list[StylelintIssue], int]:
    """Parse a single per-file result object into issues.

    Args:
        source_result: One entry from the stylelint JSON array.
        source: Resolved source file path for the entry.

    Returns:
        Usable issues and the count of malformed containers or records.
    """
    issues: list[StylelintIssue] = []
    failures = 0
    for key, default_rule in (
        ("warnings", ""),
        ("parseErrors", "parseError"),
        ("invalidOptionWarnings", "invalidOption"),
    ):
        # warnings is required; the two metadata arrays are optional.
        records = source_result.get(key, None if key == "warnings" else [])
        if not isinstance(records, list):
            failures += 1
            continue
        for record in records:
            issue = _parse_warning(record, source, default_rule)
            if issue is None:
                failures += 1
            else:
                issues.append(issue)
    return issues, failures


def _parse_warning(
    warning: Any,
    source: str,
    default_rule: str = "",
) -> StylelintIssue | None:
    """Parse a single stylelint warning object into a StylelintIssue.

    Args:
        warning: A single warning/parseError dictionary from stylelint output.
        source: Source file path the warning belongs to.
        default_rule: Fallback rule code when the entry has no ``rule`` key.

    Returns:
        StylelintIssue if parsing succeeds, None otherwise.
    """
    if not isinstance(warning, dict):
        return None

    text = str(warning.get("text", "") or "")
    if not text:
        return None

    rule = str(warning.get("rule", "") or default_rule)
    severity = str(warning.get("severity", "error") or "error")

    line_raw = warning.get("line", 0)
    column_raw = warning.get("column", 0)
    try:
        line = int(line_raw) if line_raw is not None else 0
    except (TypeError, ValueError):
        line = 0
    try:
        column = int(column_raw) if column_raw is not None else 0
    except (TypeError, ValueError):
        column = 0

    return StylelintIssue(
        file=source,
        line=line,
        column=column,
        code=rule,
        message=text,
        severity=severity,
        fixable=False,
    )


def _is_ignored_record(record: Any) -> bool:
    """Report whether a source record is one Stylelint ignored.

    Stylelint 18 writes an ignored file as a full record: ``source``,
    ``ignored: true``, an empty ``warnings`` list and empty
    ``deprecations``, ``invalidOptionWarnings`` and ``parseErrors`` lists.
    Anything else (missing, null or non-list ``warnings``, or any entry in
    the metadata lists) is not a clean ignored record.

    Args:
        record: One element of Stylelint's JSON report.

    Returns:
        True for a record flagged ``"ignored": true`` whose ``warnings`` is
        an empty list and whose metadata lists are empty or absent.
    """
    if not isinstance(record, dict) or record.get("ignored") is not True:
        return False
    if record.get("warnings") != []:
        return False
    return all(
        record.get(key, []) == []
        for key in ("deprecations", "invalidOptionWarnings", "parseErrors")
    )


def stylelint_report_lints_no_file(output: str | None) -> bool:
    """Report whether a well-formed Stylelint report shows no file was linted.

    Stylelint's own ignore configuration can exclude every file lintro passes
    (#3393). With ``--allow-empty-input``, files excluded by
    ``.stylelintignore`` leave the report empty (``[]``), and files excluded by
    the ``ignoreFiles`` config key appear as records flagged
    ``"ignored": true``. A report holding only such records proves that
    nothing was inspected. A report where only some records are ignored is an
    ordinary run.

    Args:
        output: The combined stdout/stderr from stylelint ``--formatter json``.

    Returns:
        True only for a complete report, with no malformed sibling, whose
        records are all ignored (or that has none).
    """
    if not output or not output.strip():
        return False
    try:
        data: Any = json.loads(output)
    except json.JSONDecodeError:
        data, failures = _extract_json_array(output)
        if failures:
            return False
    return isinstance(data, list) and all(_is_ignored_record(r) for r in data)
