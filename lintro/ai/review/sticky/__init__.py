"""The sticky mission-control comment a review edits in place on every run.

The sticky is the PR's *mission control* (#1909, epic #1905): a living status
board rewritten on every round. It leads with the derived readiness verdict and
the round-over-round delta, then indexes the open findings — it deliberately
does **not** repeat the finding detail that already lives on the inline
comments.

Layout of the round board, top to bottom, as ``body.round_sections`` orders
it:

1. header — ``## 🔎 Lintro Review — <verdict>``
2. the incomplete-coverage banner and the coverage line, when either applies
3. ``Summary`` — headline plus walkthrough bullets, severity-marked when a
   bullet is tied to an open P1/P2
4. ``Why it's blocked`` — the model's reasoning and the files needing
   attention
5. the one-line rows: degraded inline posting, dropped suggestions, limited
   coverage, cross-chunk contradictions
6. ``Findings · Round N`` — the delta table, one row per open finding and per
   finding fixed this round
7. the folded finding detail, when inline comments could not be posted
8. the fix-all agent prompt panel, scoped to *all* still-open findings
9. *This run* badges, two single-row tables (model-first ordering)
10. the structured-checklist appendix, when the display mode asks for it
11. ``---`` then exactly one ``🕘 Run history`` collapsible, which carries the
    severity tiles and the per-round expanders
12. a one-line footer

A state-only re-render (``body.state_sections``) is the same list minus every
section that describes a run that did not happen: summary, reasoning, the
prompt panel and the *This run* badges.

Some renderers in ``sections`` and ``findings`` are not on either list —
``_readiness_pill``, ``_verdict_explainer``, ``_delta_line`` and
``_open_findings_section``, whose content the ``Findings`` table and the
header absorbed in the #2157 redesign. They are left where the split found
them: reviving or deleting a section is a comment-design decision (#1905), and
this package's job is to render whichever set that design settles on.

Two invariants the renderer enforces, neither of them implemented here:

* **No nested ``<details>``.** Every collapsible is top level; the run history
  carries plain tables and the degraded fold-in flattens finding detail.
* **The comment (body + state block) always fits ``MAX_COMMENT_CHARS``.**
  Oldest run history is pruned first, then resolved findings, then open
  findings — each with a visible marker, never a silent drop. That invariant
  lives in ``github_contract.py``, which the error surface consumes too, so the
  two posting paths cannot drift apart again (#2303, epic #1974).

The modules underneath are layered: ``cells`` formats one value, ``sections`` /
``findings`` / ``history`` render one block, ``body`` orders those blocks into
:class:`~lintro.ai.review.github_render.Section` lists, and ``assembly`` hands
them to the shared ``assemble`` pipeline (#2304).
"""

from __future__ import annotations

from lintro.ai.review.sticky.assembly import (
    advance_review_state,
    build_sticky_bodies,
    build_sticky_comment,
    render_state_sticky,
)
from lintro.ai.review.sticky.state import (
    matcher_reviewed_paths,
    parse_review_state,
    parse_review_state_v2,
    stamp_comment_ids,
)

__all__ = [
    "advance_review_state",
    "build_sticky_bodies",
    "build_sticky_comment",
    "matcher_reviewed_paths",
    "parse_review_state",
    "parse_review_state_v2",
    "render_state_sticky",
    "stamp_comment_ids",
]
