"""Response parser for the AI-powered ``idiom-review`` tool.

Turns raw AI provider responses into structured :class:`IdiomReviewIssue`
objects. The parser is deliberately fault-tolerant: malformed, partial, or
empty responses yield an empty list and a logged warning rather than an
exception, matching the fault-tolerance conventions used by the other
parsers in :mod:`lintro.parsers`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from lintro.parsers.idiom_review.idiom_review_issue import IdiomReviewIssue

# Confidence -> display severity. ``HINT`` normalizes to ``INFO`` via
# ``normalize_severity_level`` but is preserved here for display fidelity.
_CONFIDENCE_TO_SEVERITY: dict[str, str] = {
    "high": "WARNING",
    "medium": "INFO",
    "low": "HINT",
}

_FENCE_RE = re.compile(
    r"```(?:json)?\s*(?P<body>.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _coerce_int(value: Any) -> int:
    """Best-effort convert a JSON value to a non-negative int.

    Args:
        value: Raw value from the parsed response.

    Returns:
        The integer value, or 0 when the value cannot be converted.
    """
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _normalize_confidence(value: Any) -> str:
    """Normalize a confidence value to ``high``/``medium``/``low``.

    Args:
        value: Raw confidence value from the response.

    Returns:
        A canonical confidence string, defaulting to ``medium``.
    """
    text = str(value).strip().lower()
    if text in _CONFIDENCE_TO_SEVERITY:
        return text
    return "medium"


def _extract_json(response: str) -> dict[str, Any] | None:
    """Extract a JSON object from a raw AI response.

    Handles bare JSON, JSON wrapped in a Markdown code fence, and JSON
    surrounded by prose. Returns ``None`` when no JSON object can be
    parsed.

    Args:
        response: Raw AI response text.

    Returns:
        The parsed JSON object, or ``None`` on failure.
    """
    if not response or not response.strip():
        return None

    candidates: list[str] = []

    # Consider every fenced block, not just the first: models sometimes emit
    # an explanatory fenced snippet before the requested JSON block.
    for fence_match in _FENCE_RE.finditer(response):
        candidates.append(fence_match.group("body"))

    candidates.append(response)

    # Fallback: slice from the first opening brace to the last closing brace.
    start = response.find("{")
    end = response.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(response[start : end + 1])

    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


class IdiomReviewParser:
    """Parse AI responses into :class:`IdiomReviewIssue` objects."""

    def parse_file_review(
        self,
        response: str,
        file_path: str,
    ) -> list[IdiomReviewIssue]:
        """Parse a per-file idiom-review response (Mode 1).

        Expects a JSON object with a ``findings`` array; each finding maps
        to one :class:`IdiomReviewIssue`.

        Args:
            response: Raw AI response text.
            file_path: Path of the reviewed file (used for issue ``file``).

        Returns:
            A list of parsed issues, empty on malformed/empty input.
        """
        data = _extract_json(response)
        if data is None:
            logger.warning(
                "[idiom-review] Could not parse file-review response for {}",
                file_path,
            )
            return []

        findings = data.get("findings")
        if not isinstance(findings, list):
            logger.warning(
                "[idiom-review] Missing 'findings' array for {}",
                file_path,
            )
            return []

        issues: list[IdiomReviewIssue] = []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            confidence = _normalize_confidence(finding.get("confidence"))
            line = _coerce_int(finding.get("line"))
            issues.append(
                IdiomReviewIssue(
                    file=file_path,
                    line=line,
                    column=_coerce_int(finding.get("column")),
                    message=str(finding.get("message", "")),
                    code=str(finding.get("code", "")),
                    severity=_CONFIDENCE_TO_SEVERITY[confidence],
                    end_line=_coerce_int(finding.get("end_line")) or line,
                    confidence=confidence,
                    suggested_idiom=str(finding.get("suggested_idiom", "")),
                ),
            )
        return issues

    def parse_duplication_review(
        self,
        response: str,
    ) -> list[IdiomReviewIssue]:
        """Parse a cross-file duplication response (Mode 2).

        Expects a JSON object with a ``duplicate_groups`` array. Each group
        shares one ``code`` and ``message`` and lists the locations where
        the duplicated logic appears; one issue is emitted per location.

        Args:
            response: Raw AI response text.

        Returns:
            A list of parsed issues, empty on malformed/empty input.
        """
        data = _extract_json(response)
        if data is None:
            logger.warning(
                "[idiom-review] Could not parse duplication response",
            )
            return []

        groups = data.get("duplicate_groups")
        if not isinstance(groups, list):
            logger.warning("[idiom-review] Missing 'duplicate_groups' array")
            return []

        issues: list[IdiomReviewIssue] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            confidence = _normalize_confidence(group.get("confidence"))
            code = str(group.get("code", "idiom/cross-file/duplicate"))
            message = str(group.get("message", ""))
            suggested = str(group.get("suggested_idiom", ""))
            severity = _CONFIDENCE_TO_SEVERITY[confidence]

            locations = group.get("locations")
            if not isinstance(locations, list):
                continue
            for location in locations:
                if not isinstance(location, dict):
                    continue
                line = _coerce_int(location.get("line"))
                issues.append(
                    IdiomReviewIssue(
                        file=str(location.get("file", "")),
                        line=line,
                        column=_coerce_int(location.get("column")),
                        message=message,
                        code=code,
                        severity=severity,
                        end_line=_coerce_int(location.get("end_line")) or line,
                        confidence=confidence,
                        suggested_idiom=suggested,
                    ),
                )
        return issues
