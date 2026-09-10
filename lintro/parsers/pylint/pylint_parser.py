"""Parser for pylint ``--output-format=json2`` output.

pylint's ``json2`` reporter emits a single JSON object with a ``messages``
array and a ``statistics`` object. Each message carries the source location
(``path``/``line``/``column``), the message id (``messageId``), the symbolic
name (``symbol``) and the human-readable ``message`` body.

The ``message`` body is preserved verbatim. That matters for
``duplicate-code`` (``R0801``), whose body is a multi-line block naming every
file in the clone set and quoting the duplicated source; rewriting or
collapsing it would throw away the only description of what is duplicated.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import extract_int_field, extract_str_field
from lintro.parsers.pylint.pylint_issue import PylintIssue


def _parse_pylint_message(item: dict[str, Any]) -> PylintIssue | None:
    """Convert one pylint ``json2`` message into a :class:`PylintIssue`.

    Args:
        item: One entry of the reporter's ``messages`` array.

    Returns:
        The parsed issue, or None when the entry names no file (pylint never
        emits such a message, so it signals a malformed payload).
    """
    path = extract_str_field(item, ["path", "absolutePath"])
    if not path:
        logger.debug(f"Skipping pylint message without a path: {item!r}")
        return None

    return PylintIssue(
        file=path,
        line=extract_int_field(item, ["line"], default=0) or 0,
        column=extract_int_field(item, ["column"], default=0) or 0,
        # Verbatim: R0801 bodies are multi-line clone reports.
        message=extract_str_field(item, ["message"]),
        code=extract_str_field(item, ["messageId"]),
        symbol=extract_str_field(item, ["symbol"]),
        message_type=extract_str_field(item, ["type"]),
    )


def parse_pylint_output(output: str | None) -> list[PylintIssue]:
    """Parse pylint ``json2`` output into issue objects.

    Args:
        output: Raw stdout from ``pylint --output-format=json2``.

    Returns:
        One :class:`PylintIssue` per reported message; empty when the run was
        clean or produced no output.

    Raises:
        json.JSONDecodeError: If non-empty output is not valid JSON, or is
            JSON that is not a json2 report (no object, or no ``messages``
            array). Callers must surface this rather than reporting a clean
            pass, because an unreadable report is not the same as no findings.
    """
    if not output or not output.strip():
        return []

    document = json.loads(output)
    if not isinstance(document, dict):
        raise json.JSONDecodeError(
            "pylint json2 output is not a JSON object",
            output,
            0,
        )

    messages = document.get("messages")
    if not isinstance(messages, list):
        # Every json2 report carries a ``messages`` array, empty on a clean
        # run. A JSON object without one is therefore not a pylint report at
        # all, and treating it as "no findings" would turn an unreadable run
        # into a green result.
        raise json.JSONDecodeError(
            "pylint json2 output has no 'messages' array",
            output,
            0,
        )

    issues: list[PylintIssue] = []
    for item in messages:
        if not isinstance(item, dict):
            logger.debug(f"Skipping non-object pylint message: {item!r}")
            continue
        issue = _parse_pylint_message(item)
        if issue is not None:
            issues.append(issue)
    return issues
