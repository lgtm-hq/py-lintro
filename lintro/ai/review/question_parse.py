"""Parse the per-PR question pass's answer, tolerantly and diagnosably (#2813).

The prompt asks for ``{"generated_questions": [{"question": ...}, ...]}``, but
models answer in nearby shapes: a bare list, or the list under one other key.
The grammar accepts exactly these list sources and rejects everything else as
before:

* ``{"generated_questions": [...]}``;
* a bare top-level list;
* an object with exactly one key, whose value is a list.

Within the list, only objects with a non-empty string ``question`` count. A
failure is classified (:class:`QuestionFailureKind`) so the degradation
record and the log say why, and the log carries a redacted, single-line
capture of the answer's start so the next occurrence is diagnosable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lintro.ai.json_response import strip_json_fences
from lintro.ai.review.enums.question_failure_kind import QuestionFailureKind
from lintro.ai.review.prompt_redaction import redact_prompt_text

__all__ = ["CAPTURE_CHARS", "ParsedQuestions", "capture_for_log", "parse_questions"]

#: How much of a failed answer the log keeps.
CAPTURE_CHARS = 500

#: The key the prompt asks for.
_QUESTIONS_KEY = "generated_questions"


@dataclass(frozen=True, slots=True)
class ParsedQuestions:
    """The usable questions in an answer, or why there were none.

    Attributes:
        questions: Question texts, in answer order, stripped of nothing.
        failure: Why the answer was unusable, or None when it was usable.
    """

    questions: tuple[str, ...] = ()
    failure: QuestionFailureKind | None = None


def parse_questions(content: str) -> ParsedQuestions:
    """Return the usable questions in *content* under the tolerant grammar.

    Args:
        content: The model's raw answer.

    Returns:
        The questions, or the failure kind when none are usable.
    """
    if not content.strip():
        return ParsedQuestions(failure=QuestionFailureKind.EMPTY)
    try:
        payload = json.loads(strip_json_fences(content=content))
    except (json.JSONDecodeError, ValueError):
        return ParsedQuestions(failure=QuestionFailureKind.NOT_JSON)
    items = _list_source(payload)
    if items is None:
        return ParsedQuestions(failure=QuestionFailureKind.NOT_LIST)
    questions = tuple(
        item["question"]
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("question"), str)
        and item["question"].strip()
    )
    if not questions:
        return ParsedQuestions(failure=QuestionFailureKind.NO_QUESTION)
    return ParsedQuestions(questions=questions)


def _list_source(payload: Any) -> list[Any] | None:
    """Return the list the grammar reads questions from, or None.

    Args:
        payload: The decoded answer.

    Returns:
        The list under ``generated_questions``, the bare list, or the only
        value of a one-key object; None for any other shape.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    if _QUESTIONS_KEY in payload:
        value = payload[_QUESTIONS_KEY]
        return value if isinstance(value, list) else None
    if len(payload) == 1:
        (value,) = payload.values()
        return value if isinstance(value, list) else None
    return None


def capture_for_log(content: str) -> str:
    """Return the answer's start for the log: redacted, bounded, one line.

    The text is model output shaped by untrusted PR content, so detected
    secrets are redacted, and it is JSON-encoded onto one line so an embedded
    newline followed by ``::`` cannot become a GitHub Actions workflow command.

    Args:
        content: The model's raw answer.

    Returns:
        A JSON string literal of at most :data:`CAPTURE_CHARS` source
        characters.
    """
    redacted = redact_prompt_text(text=content, source="question pass answer")
    return json.dumps(redacted[:CAPTURE_CHARS])
