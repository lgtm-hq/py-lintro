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

from lintro.ai.json_response import iter_json_candidates
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

    Every JSON value in the answer is a candidate, and the first that yields
    questions wins, so a stray bracketed citation in prose (``see [5]``)
    cannot shadow the real payload beside it (#2826). When none yields:

    * an answer that is wholly a JSON scalar is ``not_json`` (retried);
    * an answer that is wholly a JSON object or list is classified by it;
    * otherwise by the first embedded object;
    * a list of objects without a question is ``no_question``;
    * prose whose only brackets are scalar lists is ``not_json`` (retried).

    Args:
        content: The model's raw answer.

    Returns:
        The questions, or the failure kind when none are usable.
    """
    if not content.strip():
        return ParsedQuestions(failure=QuestionFailureKind.EMPTY)
    candidates = list(iter_json_candidates(content=content))
    for candidate in candidates:
        questions = _questions_in(candidate.payload)
        if questions:
            return ParsedQuestions(questions=questions)
    wholes = [c.payload for c in candidates if c.whole]
    if wholes and not isinstance(wholes[0], dict | list):
        # A whole-answer scalar (``null``, ``42``, ``"n/a"``) holds no
        # object-shaped JSON: not_json, so it gets the one retry.
        return ParsedQuestions(failure=QuestionFailureKind.NOT_JSON)
    objects = [c.payload for c in candidates if isinstance(c.payload, dict)]
    decisive = wholes or objects
    if decisive:
        items = _list_source(decisive[0])
        kind = (
            QuestionFailureKind.NOT_LIST
            if items is None
            else QuestionFailureKind.NO_QUESTION
        )
        return ParsedQuestions(failure=kind)
    if any(
        isinstance(c.payload, list) and any(isinstance(i, dict) for i in c.payload)
        for c in candidates
    ):
        return ParsedQuestions(failure=QuestionFailureKind.NO_QUESTION)
    return ParsedQuestions(failure=QuestionFailureKind.NOT_JSON)


def _questions_in(payload: Any) -> tuple[str, ...]:
    """Return the question texts one candidate carries under the grammar.

    Args:
        payload: A decoded JSON value.

    Returns:
        The non-empty ``question`` strings of its list source, in order.
    """
    items = _list_source(payload)
    if items is None:
        return ()
    return tuple(
        item["question"]
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("question"), str)
        and item["question"].strip()
    )


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
        A JSON string literal of the first :data:`CAPTURE_CHARS` characters
        of the redacted answer (redaction first, so a cut can only split a
        ``[REDACTED]`` marker, never a secret).
    """
    redacted = redact_prompt_text(text=content, source="question pass answer")
    return json.dumps(redacted[:CAPTURE_CHARS])
