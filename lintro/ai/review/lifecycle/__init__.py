"""The single owner of what happens to a review's comments (#2305, epic #1974).

A review writes three things on a pull request and edits them for the rest of
its life: the mission-control board, the run-history archive, and the inline
thread under each finding. Every one of them raises the same question — is
this created, edited in place, or replaced? — and before this package the
success path, the error path, the converged path and the CLI's state helpers
each answered it separately. That is how the same class of bug (#1866) came to
exist twice.

The modules are layered, each owning one decision:

* ``decision`` — the pure ``decide()`` that answers create / update /
  supersede for a comment of any kind, and nothing else.
* ``comments`` — carries a decision out against the API, feeding GitHub's
  answer back through ``decide`` rather than branching on it a second time.
* ``markers`` — the hidden marker that ties a comment to its finding, and the
  URL that links back to it.
* ``banners`` — renders the stamp a settled thread carries.
* ``threads`` — decides which thread earns which stamp, and resolves the ones
  that are done.
* ``round`` — the success path's single pass over the pull request's inline
  comments: stamp what settled, capture what was created.
* ``state`` — reads the state a round continues from and writes the advanced
  one back, on the same side of the #2154 trust boundary it was read from.

Nothing is re-exported here. A caller imports from the module that owns what
it needs, so the layering above is visible at every call site rather than
flattened into one name.
"""

from __future__ import annotations
