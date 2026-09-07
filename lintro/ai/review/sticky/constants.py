"""Verdict-keyed lookup tables and render caps for the sticky board.

The tables are guarded at import time: a verdict added without a rendering
entry fails loudly here rather than as a ``KeyError`` mid-render on a live
pull request.
"""

from __future__ import annotations

import re

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.review_finding import Severity

#: Emoji rendered next to each readiness verdict's label.
VERDICT_EMOJI: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "⛔",
    ReviewVerdict.CHANGES_REQUESTED: "⚠️",
    ReviewVerdict.NITS_ONLY: "🟡",
    ReviewVerdict.READY: "✅",
    ReviewVerdict.INCOMPLETE: "⚠️",
}

#: Heading used for the reasoning section, per verdict.
_REASONING_HEADINGS: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "Why it's blocked",
    ReviewVerdict.CHANGES_REQUESTED: "Why changes are requested",
    ReviewVerdict.NITS_ONLY: "Why it's flagged",
    ReviewVerdict.READY: "Why it's ready",
    ReviewVerdict.INCOMPLETE: "Why the verdict is withheld",
}

#: Noun naming the finding class that decides each verdict, for the pill.
_VERDICT_NOUNS: dict[ReviewVerdict, str] = {
    ReviewVerdict.BLOCKED: "blocker",
    ReviewVerdict.CHANGES_REQUESTED: "warning",
    ReviewVerdict.NITS_ONLY: "nit",
    ReviewVerdict.READY: "finding",
    ReviewVerdict.INCOMPLETE: "file",
}

#: Severity that decides each verdict, used to count the pill's subject.
#: ``READY`` is deliberately absent — it is decided by the *absence* of open
#: findings, and :func:`_readiness_pill` returns before consulting this table.
_VERDICT_SEVERITY: dict[ReviewVerdict, Severity] = {
    ReviewVerdict.BLOCKED: Severity.P1,
    ReviewVerdict.CHANGES_REQUESTED: Severity.P2,
    ReviewVerdict.NITS_ONLY: Severity.P3,
}

# Import-time exhaustiveness guards, twins of the one in
# :mod:`lintro.ai.review.verdict`: a verdict added without a rendering entry
# must fail loudly at import rather than as a KeyError mid-render on a PR.
for _table_name, _table in (
    ("VERDICT_EMOJI", VERDICT_EMOJI),
    ("_REASONING_HEADINGS", _REASONING_HEADINGS),
    ("_VERDICT_NOUNS", _VERDICT_NOUNS),
):
    _missing = set(ReviewVerdict) - set(_table)
    if _missing:  # pragma: no cover - guards a future verdict
        raise RuntimeError(f"{_table_name} missing entries for: {_missing}")

# _VERDICT_SEVERITY is guarded separately because READY is deliberately absent
# from it. Without this a new non-READY verdict would pass the loop above and
# then KeyError inside _readiness_pill on a live PR.
_missing = (
    set(ReviewVerdict)
    - {ReviewVerdict.READY, ReviewVerdict.INCOMPLETE}
    - set(_VERDICT_SEVERITY)
)
if _missing:  # pragma: no cover - guards a future verdict
    raise RuntimeError(f"_VERDICT_SEVERITY missing entries for: {_missing}")

#: Emoji marking a tracked entry that is a question rather than a finding.
_QUESTION_EMOJI = "❓"

#: Matches a ``<details>``/``</details>`` tag in untrusted model text. The folded
#: finding detail sits *inside* a collapsible, so a model-written closing tag
#: would end it early and break the sticky's one-level-only structure.
_DETAILS_TAG_RE = re.compile(r"<(/?)(details|summary)\b", re.IGNORECASE)

#: Maximum characters of a finding title rendered in a table cell.
_TITLE_LIMIT = 160

#: End of the first sentence of a round narrative. Terminators other than the
#: period are matched too: a headline ending in "?" or "!" is one sentence, and
#: splitting on ". " alone would persist the whole paragraph after it.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")

#: Maximum characters of a stored per-round narrative, on the way in (it is
#: persisted in the state blob, which competes for the same size cap) and on
#: the way out.
_NARRATIVE_LIMIT = 200
