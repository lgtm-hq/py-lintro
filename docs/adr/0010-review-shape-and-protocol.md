# ADR-0010: AI review shape and protocol

## Status

Accepted

## Context

Epic [#2555](https://github.com/lgtm-hq/py-lintro/issues/2555) opens with the
observation that the review pipeline "grew by incident": a rerun that ignored its own
checkpoints, a posting step that outlived its token so a "changes requested" verdict was
never posted while the check went green, a rate-limited lane that inflated the job
timeout to 120 minutes, CLI transports with four separate correctness defects, and a
chunk pipeline that discarded paid-for findings when a deeper pass timed out. Each got a
correct, small fix. None came from a written statement of what the mechanism must
guarantee.

Step 1 of that epic therefore asks for a design record under `docs/adr/` before any
further implementation. The design pass itself has already happened: the owner made the
decisions on 2026-09-10 on the private long-range track, lgtm-hq/lintro-ops#24. Because
that tracker is private, the decisions were not visible from py-lintro, and #2555 as
originally written would have blocked phase 0 on a design pass that was already done.
Milestone 0 item 0.4 — transferred here as
[#2574](https://github.com/lgtm-hq/py-lintro/issues/2574) — records them in the public
repository so the epic can point at a real document.

This ADR is that record. It states decisions A to D as #2574 states them and names, for
each, the issue that delivers it. It does not re-open the design, and it does not add
decisions that were not made on 2026-09-10.

Three earlier ADRs already fix parts of the surrounding surface and are assumed rather
than restated here: [ADR-0006](0006-ai-effective-config-and-review-execution.md) (one
effective AI config and a shared review path),
[ADR-0007](0007-review-resume-and-artifact-state.md) (file-level resume and artifact
state) and [ADR-0008](0008-ai-review-architecture-invariants.md) (the invariants the
review architecture holds). [ADR-0009](0009-ai-provider-plugin-contract.md) fixes the
provider seam that makes decision A's transport neutrality expressible.

## Decision

### A. Review shape

`lintro review` is shaped as:

- **Small parallel file-group chunks.** The unit of review is a group of related files,
  small enough to review at depth, and chunks run in parallel.
- **Findings-only output.** A chunk call returns findings, not prose about the chunk.
- **One synthesis call.** A single call per round reasons over the merged findings and
  the whole-PR changed-file list, so bugs that span two chunks are not lost to chunk
  isolation.
- **One verification call.** A single call per round checks findings before they are
  posted.
- **Transport-neutral.** The shape is the same on the API and CLI transports; a
  transport is a way to reach a provider, not a different review.
- **No per-call findings cap.** A chunk is not told to stop at N findings. Removing the
  cap is an explicit exit criterion of #2555.
- **Posting tiers.** Not every finding earns an inline thread. Findings are tiered, and
  only the tiers that clear the posting policy open threads; the rest are reported
  without blocking.

Delivered by: [#2269](https://github.com/lgtm-hq/py-lintro/issues/2269) (cross-chunk
synthesis, shipped opt-in and off by default — "Cross-chunk synthesis" in
[`docs/ai-features.md`](../ai-features.md)),
[#2283](https://github.com/lgtm-hq/py-lintro/issues/2283) (the per-call cap surface and
its retirement), [#2572](https://github.com/lgtm-hq/py-lintro/issues/2572) (posting
tiers: the confidence gate and the notes block, `ai.review_inline_min_confidence` and
`ai.review_post_questions_inline`), and
[#2554](https://github.com/lgtm-hq/py-lintro/issues/2554) with
[#2515](https://github.com/lgtm-hq/py-lintro/issues/2515) for transport neutrality.

### B. Review protocol

The review runs as six layers. A layer is a source of trust, not a pipeline stage name.

| #   | Layer                                                                                                            | Delivered by                                                                     |
| --- | ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 1   | **Facts from deterministic tools.** The model is given the linters' results for the PR before it speaks.         | [#2571](https://github.com/lgtm-hq/py-lintro/issues/2571) (milestone 0 item 0.1) |
| 2   | **Post-change context.** The model reasons about the combined post-PR state, not a per-chunk slice of it.        | [#2269](https://github.com/lgtm-hq/py-lintro/issues/2269)                        |
| 3   | **Rubric plus generated questions.** A fixed rubric, plus questions the model generates for this change.         | lintro-ops #24 milestone 0                                                       |
| 4   | **Refute before posting.** A finding is challenged before it becomes an inline thread.                           | lintro-ops #24 milestone 0; the verification call in decision A                  |
| 5   | **Human-gated memory.** What the reviewer carries between rounds is admitted by a human, not by the model alone. | lintro-ops #24 milestone 0                                                       |
| 6   | **Eval-gated changes.** A change to the reviewer ships when the eval corpus says it is an improvement.           | the eval harness milestone, which milestone 0 runs ahead of                      |

Layers 1 to 5 are milestone 0 work. Layer 6 belongs to the eval harness milestone; #2555
records that milestone 0 "runs ahead of the eval harness and is transferred here issue
by issue at pickup".

### C. Tracking

lintro-ops #24 is the private long-range track. Its **milestone 0** (review shape and
protocol, 11 issues) runs ahead of M1, and its issues are transferred into py-lintro one
by one at pickup, verbatim. [#2555](https://github.com/lgtm-hq/py-lintro/issues/2555) is
the public umbrella and the home for every transferred issue; it is where the work is
visible to contributors who cannot see lintro-ops.

### D. Interim merge policy

The review check reports a non-success status whenever `findings_coverage_complete` is
false. While the routine CLI findings cap trips that on every round, a partial red is
handled as follows:

- It **blocks** a PR that touches `lintro/ai/**` or the AI workflows.
- It is **waivable elsewhere** once every posted finding has been triaged and a waiver
  comment names the cap.
- It is **retired when [#2283](https://github.com/lgtm-hq/py-lintro/issues/2283)
  lands**, which removes the waiver path.

## Guarantees

The four headings are #2555's. Each row names the issue or the code that delivers it.

| Guarantee           | What it means here                                                                                                                                                                  | Delivered by                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Robustness**      | A provider outage, a 429 storm and a token expiry each produce a documented, user-visible outcome rather than a silent green.                                                       | [#2522](https://github.com/lgtm-hq/py-lintro/issues/2522) (posting token), [#2554](https://github.com/lgtm-hq/py-lintro/issues/2554) (CLI transport correctness), [#2515](https://github.com/lgtm-hq/py-lintro/issues/2515) with [#2481](https://github.com/lgtm-hq/py-lintro/issues/2481) (CI lanes), `lintro/ai/review/coverage_degradation.py`                                                                                                                                             |
| **Reliability**     | A green check means a complete review; a partial review cannot post as a plain success; a rerun resumes instead of restarting; the job has one time budget derived from its inputs. | [#2545](https://github.com/lgtm-hq/py-lintro/issues/2545) and `scripts/ci/classify_review_outcome.py` (`findings_coverage_complete`), [#2283](https://github.com/lgtm-hq/py-lintro/issues/2283) (cap reported only when actually hit), [#2506](https://github.com/lgtm-hq/py-lintro/issues/2506) (timeout, concurrency, resume), [#2338](https://github.com/lgtm-hq/py-lintro/issues/2338) (resume across reruns, review once per head), [ADR-0007](0007-review-resume-and-artifact-state.md) |
| **Flexibility**     | The shape is transport-neutral and provider-neutral; posting tiers and the protocol layers are configurable rather than hardcoded.                                                  | [ADR-0009](0009-ai-provider-plugin-contract.md) (provider plugin contract), [#2572](https://github.com/lgtm-hq/py-lintro/issues/2572) (`ai.review_inline_min_confidence`, `ai.review_post_questions_inline`), [#2571](https://github.com/lgtm-hq/py-lintro/issues/2571) (`lintro review --lint-report`)                                                                                                                                                                                       |
| **Maintainability** | The pipeline stays small enough to reason about, with one sub-issue per PR and this ADR updated in the same PR whenever a guarantee lands.                                          | [ADR-0008](0008-ai-review-architecture-invariants.md), [#1972](https://github.com/lgtm-hq/py-lintro/issues/1972) (orchestrator decomposition), [#2555](https://github.com/lgtm-hq/py-lintro/issues/2555) step 3                                                                                                                                                                                                                                                                               |

## Consequences

There is now one document that says what the reviewer promises, so #2555 can point at it
instead of asking for a design pass that already happened, and the next incident is a
gap in this ADR rather than a new hot-fix.

The cost is that this ADR must be kept current: #2555 step 3 requires it to be updated
in the same PR as the guarantee that lands, so a stale row here is a review defect, not
documentation debt. Layers 3 to 5 of decision B are named here before their py-lintro
issues exist — they are still tracked only in lintro-ops #24 milestone 0 and are filled
in as each is transferred at pickup.

Decision D is explicitly interim. #2283 closed as completed on 2026-09-10, so its
retirement condition has been met and the waiver path is expected to be withdrawn; the
blocking half of D — a partial red blocks PRs touching `lintro/ai/**` or the AI
workflows — is unchanged by that and remains in force. Removing the cap itself is
tracked as an exit criterion of #2555 rather than as a separate decision here.

Recording the decisions publicly also fixes them publicly. Reopening A to D is a new ADR
that supersedes this one, not an edit — the edits this ADR invites are the delivery
columns, as each issue lands.

## References

- [#2574](https://github.com/lgtm-hq/py-lintro/issues/2574) — this ADR (lintro-ops
  milestone 0 item 0.4, copied from lgtm-hq/lintro-ops#43)
- [#2555](https://github.com/lgtm-hq/py-lintro/issues/2555) — epic: harden the AI review
  mechanism; step 1 asks for this record
- [#2571](https://github.com/lgtm-hq/py-lintro/issues/2571),
  [#2572](https://github.com/lgtm-hq/py-lintro/issues/2572),
  [#2573](https://github.com/lgtm-hq/py-lintro/issues/2573) — milestone 0 items
  transferred so far
- [#2269](https://github.com/lgtm-hq/py-lintro/issues/2269),
  [#2283](https://github.com/lgtm-hq/py-lintro/issues/2283),
  [#2338](https://github.com/lgtm-hq/py-lintro/issues/2338),
  [#2506](https://github.com/lgtm-hq/py-lintro/issues/2506),
  [#2515](https://github.com/lgtm-hq/py-lintro/issues/2515),
  [#2522](https://github.com/lgtm-hq/py-lintro/issues/2522),
  [#2545](https://github.com/lgtm-hq/py-lintro/issues/2545),
  [#2554](https://github.com/lgtm-hq/py-lintro/issues/2554) — guarantee delivery
- [#2288](https://github.com/lgtm-hq/py-lintro/issues/2288) (roadmap, AI track),
  [#2553](https://github.com/lgtm-hq/py-lintro/issues/2553) (findings intake)
- lgtm-hq/lintro-ops#24 (private long-range track, milestone 0)
- [ADR-0006](0006-ai-effective-config-and-review-execution.md),
  [ADR-0007](0007-review-resume-and-artifact-state.md),
  [ADR-0008](0008-ai-review-architecture-invariants.md),
  [ADR-0009](0009-ai-provider-plugin-contract.md)
- [`docs/ai-features.md`](../ai-features.md) — "Confidence gate on inline posting",
  "Review coverage completeness", "Cross-chunk synthesis"
