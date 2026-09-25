"""Why the per-PR question pass produced no usable questions (#2813)."""

from __future__ import annotations

from enum import StrEnum, auto


class QuestionFailureKind(StrEnum):
    """The kind of a failed per-PR question pass.

    Members:
        EMPTY: The answer was blank.
        NOT_JSON: The answer held no parseable JSON.
        NOT_LIST: JSON, but none of the accepted shapes carried a list.
        NO_QUESTION: A list, but no item was an object with a non-empty
            ``question`` string.
        TURN_LIMIT: The CLI call stopped at its turn limit before answering.
        CALL_FAILED: Any other provider error on the call.
    """

    EMPTY = auto()
    NOT_JSON = auto()
    NOT_LIST = auto()
    NO_QUESTION = auto()
    TURN_LIMIT = auto()
    CALL_FAILED = auto()


#: The kinds one more attempt can plausibly fix (ruling 21 on #2813): a call
#: cut off at its turn limit, and an answer that was not JSON. A blank answer,
#: a well-formed answer of the wrong shape, and a provider error are not
#: retried.
RETRIED_KINDS: frozenset[QuestionFailureKind] = frozenset(
    {QuestionFailureKind.TURN_LIMIT, QuestionFailureKind.NOT_JSON},
)
