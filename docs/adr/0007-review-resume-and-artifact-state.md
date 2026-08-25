# ADR-0007: File-level review resume and artifact-backed review state

## Status

Accepted

## Context

`lintro review --pr` re-reviewed the full base..head diff on every push. Chunk ordering
is deterministic, so under a cost cap every round spent its budget on the same leading
chunks. Partial reviews could render clean. The sticky comment stored authoritative
state in an editable hidden blob.

## Decision

1. Coverage identity is `(path, normalized patch hash)`. `reviewed_sha` is metadata. The
   hash covers only `+`/`-` lines.
2. Invalidation is never-reviewed, directly changed, semantic-group mate, one-hop Python
   import, or a guarded `flagged_files` proposal. Broadcast files (pyproject, lockfiles,
   `conftest.py`) do not fan out. Queue order under a cap: never-reviewed → directly
   changed → model-flagged → group/import-invalidated. Same-hash inheritance is
   content-addressed: any eligible file whose current hash already has a reviewed
   representative is covered. Unserved group/import pending pairs and model flags
   persist until that path is covered this round (including inherited coverage). The
   same `(path, hash)` may be flagged only once; a repeat flag cannot re-queue that
   unchanged file.
3. Flag/env caps enforce on every cost basis. YAML enforces on `billed` and `estimated`,
   and is advisory on `unpriceable`. Overlay `uncapped` lifts the ceiling; overlay `0`
   is rejected as ambiguous.
4. Coverage-at-HEAD below 100% of review-eligible files forces `INCOMPLETE`. READY
   requires full coverage and no blocking findings. `lintro review` exit codes stay
   0/1/2; the CI classifier reddens INCOMPLETE.
5. Authoritative CI state lives in workflow artifacts. The sticky is pure rendering.
   Local runs use `.lintro-cache/ai/review-state/` with no local↔CI sync. Missing or
   untrusted state fails toward more review.

## Consequences

Capped reviews converge across rounds. A quiet re-review makes zero provider calls. A
partial review cannot render clean or pass the AI Review check. Large capped API-key PRs
are red on round 1 by design. A mid-round **timeout** is the same class of stop as a
cost cap: coverage and this-run findings already written are persisted (incremental
`part-*.json` under `LINTRO_REVIEW_STATE_DIR`) and the next round resumes. A SIGTERM
after a finished chunk must not lose that chunk's coverage or issues. The wrapper
uploads those parts from inside the review step: a cancelled Actions job skips later
`if: always()` uploads. The post-wait inline upload is capped at 2s (Create/PUT/Finalize
together, plus a `timeout(1)` hard kill) so classify still runs inside GitHub's ~7.5s
SIGTERM grace. After `wait` reports 143, the persisted envelope decides the outcome:
incomplete coverage is INCOMPLETE, and complete coverage stays REVIEWED.

`conftest.py` is a known semantic hole (test-wide fixtures) left on the revisit list.

## References

Epic #2156; #2154; #2157; ADR-0006.
