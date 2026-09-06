"""The sticky mission-control comment a review edits in place on every run.

The sticky is the PR's *mission control* (#1909, epic #1905): a living status
board rewritten on every round. It leads with the derived readiness verdict and
the round-over-round delta, then indexes the open findings — it deliberately
does **not** repeat the finding detail that already lives on the inline
comments.

Layout, top to bottom:

1. header — ``🔎 Lintro Review · round N · commit <sha>``
2. readiness pill, the verdict rubric as fine-print directly under it, then the
   delta line
3. ``Summary`` — headline plus walkthrough bullets, severity-marked when a
   bullet is tied to an open P1/P2
4. ``Why it's blocked`` — the model's reasoning and the files needing
   attention
5. severity tiles (blockers / warnings / nits / fixed)
6. ``Open findings`` — one line per finding, titles only
7. the fix-all agent prompt panel, scoped to *all* still-open findings
8. ``Resolved`` — struck-through titles with their fixing commit
9. *This run* badges, two single-row tables (model-first ordering)
10. ``---`` then exactly one ``🕘 Run history`` collapsible
11. a one-line footer

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
