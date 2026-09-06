# AI Effective Config and Review Execution

This page is the architecture index for Lintro's AI review execution seams. The
normative decision record is
[ADR-0006](../adr/0006-ai-effective-config-and-review-execution.md) (epic
[#1972](https://github.com/lgtm-hq/py-lintro/issues/1972)).

## Ownership boundaries

| Concern                                             | Owner today                                                                                | Target                                         |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| Effective `ResolvedAIConfig` for one invocation     | `resolve_effective_ai_config()` (#2299), the one caller of `AIConfig.resolve_from_mapping` | Same resolver; #1923 extends it                |
| Invocation transport / timeout / cost-cap overrides | `AICliOverrides` on that resolver, for lint and review alike                               | Same resolver pipeline (#1970 / #1923 / #2024) |
| Monotonic cost-cap clamp                            | MCP adapter (`resolve_budget_policy`)                                                      | Shared domain prep; adapters keep policy       |
| Diff review preparation                             | `prepare_review` / `execute_review` (#2300)                                                | Done (Phase 3)                                 |
| Review execution facade                             | `run_review` / `run_review_async`                                                          | Unchanged facade; internals split (Phase 4)    |
| Provider client `aclose()` API                      | Not yet (#1885)                                                                            | Provider-side only in #1885                    |
| Provider close call-site wiring                     | N/A until #1885                                                                            | Phase 5 of #1972                               |

## Shared preparation (`lintro/ai/review/preparation.py`)

Since #2300 both the CLI (`lintro review`) and MCP (`lintro_review`) run one shared path
— `ReviewRunRequest` → `prepare_review` → `PreparedReview` → `execute_review`:

1. Each adapter resolves AI config through `resolve_effective_ai_config` — one resolver
   pipeline for both (#2299). What each passes into it differs by design: CLI review
   passes its flags as `AICliOverrides`, which may change values and stamp `flag`
   provenance, while MCP passes none because its one per-call knob (`max_cost_usd`) is a
   downstream monotonic clamp (`resolve_budget_policy`) rather than an overlay. Given
   identical resolver inputs the two produce identical values _and_ provenance; the
   parity suite asserts exactly that.
2. Each adapter gates on `ai.review` being enabled (adapter-specific error shape) and
   builds a `ReviewRunRequest` from its own argument surface.
3. `prepare_review` then does the deterministic, provider-free work once: apply the
   run's timeout and the transport profile, collect the review context (honouring
   `ai.exclude_paths` on both surfaces), classify changed files, select and format the
   checklist, optionally build the lint digest, resolve sensitivity, and resolve custom
   agents from the request's `CustomAgentMode` (both adapters forward
   `review.custom_agents`).
4. Each adapter constructs its own provider — provider lifetime stays with the
   constructing surface until #1972 Phase 5 — and calls `execute_review`, which is the
   single `run_review` call site.
5. Each adapter translates failures into its own contract.

`ReviewExecutionPolicy` carries what is genuinely adapter-only into `execute_review`:
terminal progress, `--context-window`, resume state, `--full`, and the CLI's cost-cap
gate. MCP runs on `DEFAULT_EXECUTION_POLICY`, whose values are `run_review`'s own
defaults. It is a frozen value object that may carry an optional progress callback — not
a hook or plugin architecture.

Adapter-only policy that must stay out of the shared layer:

- CLI: Click errors, progress UI, JSON/terminal rendering, GitHub posting, exit
  `0`/`1`/`2`.
- MCP: workspace locking, budget clamp, structured tool envelopes, no posting.
- Advisory-only CLI mode: master-switch semantics without the diff-review sub-toggle.

## Orchestrator phase plan

`run_review` remains the stable facade. Phase 4 decomposes internals into
runner/session, planning/chunks, prompts/passes, response pipeline, merge/filter, and
metadata modules without changing prompts, findings, severity, or exit semantics. Every
provider call continues through `call_ai`; prompt redaction remains mandatory.

### Session options (`lintro/ai/review/session.py`, #2301)

The first slice of Phase 4 ends the keyword wall. `run_review` still takes the run's
settings as keywords — that is the public facade every adapter calls — but it now packs
them into a frozen `ReviewSessionOptions` and hands that single object down;
`run_review_async` takes `(context, options=...)`. New settings are added as a field on
the options object rather than as another keyword threaded through each layer. The
graceful-stop predicates (`is_cost_cap_stop`, `cost_cap_reason`, `is_timeout_stop`,
`timeout_reason`) and the `aborted_before_completion` wrapper live in the same module,
since deciding whether a run stopped gracefully is session-level, not chunk-level.

### Prompt construction (`lintro/ai/review/prompts.py`, #2301)

The second slice moves the two chunk prompt builders — `build_review_prompt` for the API
transport and `build_git_native_review_prompt` for CLI-backed providers — and the
non-diff token estimate `estimate_prompt_overhead` out of the orchestrator. The shared
render inputs (chunk, context, checklist text and count, interaction paths, lint digest,
generated checklist rows, strictness section, findings cap) travel as one frozen
`PromptInputs`; only the git-native diff-delivery flags stay as separate keywords, since
they are the one thing the two builders do not share. `redact_prompt_text` and
`make_boundary_marker` now fire inside this module, which makes it the redaction choke
point for prompt bytes: the git-native builder still embeds the redacted diff unless the
caller explicitly opts out. The emitted bytes are unchanged and the #2298 prompt goldens
pass without regeneration; the orchestrator re-exports both builders so the facade is
untouched.

### Cross-chunk merge (`lintro/ai/review/merge.py`, #2301)

The third slice moves the fold-back stage out of the orchestrator: the partial a chunk
returns (`ChunkReviewPartial`), the six `merge_*` functions that combine partials into
one `ReviewResult`, the `finalize_partials` wrapper that merges and then applies the
sensitivity policy, and `parse_review_response`. The merge rules are behaviour, not
implementation detail, and the #2298 merge goldens pin them: findings deduplicate by
`(file, line, title)` in first-seen order, a `yes` checklist answer from any chunk beats
a `no` from any other regardless of evidence, chunk summaries join in chunk order,
walkthrough bullets deduplicate by text and cap at `MAX_WALKTHROUGH_BULLETS`, and the
first chunk to speak wins verdict prose and per-file assessments. The orchestrator
re-exports the merge names, so the facade and every external importer are untouched.

### Per-chunk passes (`response_pipeline.py`, the two pass modules, #2301)

The fourth slice moves everything that happens _inside_ one chunk out of the
orchestrator, leaving it the chunk scheduler it is meant to be.

`lintro/ai/review/response_pipeline.py` owns the main round-trip: `invoke_chunk_review`
builds the prompt through `PromptInputs`, calls the provider, and — when CLI transport
hits its output-token ceiling mid-JSON — retries once with a tighter findings cap
(#1967). Both the cap and the retry are recorded as `CoverageDegradation` entries, so a
capped chunk can never present as an unlimited one (#2003). Its sixteen inputs travel as
one frozen `ChunkReviewRequest` rather than a keyword wall.
`parse_review_payload_with_recovery` runs the parse ladder — parse, then at most one
schema-reminder retry when the timeout budget allows one, then recovery of the prose as
unstructured findings — so a paid-for answer is never discarded (#1853); it folds the
retry's usage in through `merge_response_usage`. `payload_to_partial` and
`parse_checklist` turn the parsed payload into the `ChunkReviewPartial` the merge layer
consumes.

`lintro/ai/review/checklist_pass.py` owns the depth-2 generated checklist:
`generate_extra_checklist` asks the model for domain-specific questions and truncates
the answer at `GENERATED_CHECKLIST_ID_STRIDE`, which is what keeps parallel chunks on
disjoint generated-id ranges so `merge_checklist_answers` cannot collide.
`lintro/ai/review/adversarial_pass.py` owns the depth-3 sweep: `run_adversarial_pass`
returns findings and usage only, and degrades to usage alone when the answer is
malformed.

`call_ai` is now bound in these three modules rather than in the orchestrator, so a test
that stubs the provider seam for a depth >= 2 run patches all three (the
`patch_review_call_ai` helper in `tests/unit/ai/review/conftest.py` does exactly that).
Behaviour is unchanged and the #2298 goldens pass without regeneration; the orchestrator
re-exports the moved names until the final slice removes the facade shims.

## Exit and error contracts

- Exit `0` — successful review, no P1 findings.
- Exit `1` — successful review with P1 findings.
- Exit `2` — no review produced (`REVIEW_ERROR_EXIT_CODE`).

CLI JSON failures and MCP review failures both build diagnosis fields through
`build_error_contract`.

## Characterization tests

Phase 1 locks the gaps listed in ADR-0006:

- `tests/unit/test_core_ai_import_boundary.py` — AC10 / #724 import edge.
- `tests/unit/ai/review/test_architecture_characterization.py` — CLI/MCP preparation,
  effective-config parity, metadata keys, error mapping, exit `0`/`1`/`2`.
- `tests/unit/ai/review/golden/` — prompt bytes, chunk plan, merge output, merged
  `ReviewResult` and `ReviewMetadata`, as plain-file goldens
  ([ADR-0008](../adr/0008-ai-review-architecture-invariants.md), #2298).
- `tests/unit/ai/review/test_cli_mcp_parity.py` — CLI/MCP parity: for equal
  `ReviewRunRequest` values over one workspace the two surfaces produce an **equal**
  `PreparedReview` (custom agents included — the fixture ships an agent file), and the
  only divergence left is the named `ReviewExecutionPolicy` allowlist. MCP's post-prep
  `with_max_cost_usd` clamp is the one thing it applies to the prepared review
  afterwards.
- `tests/unit/ai/review/test_architecture_characterization_1972.py` — gap coverage:
  config-resolution idempotence, shared `run_review` kwargs, error-contract body parity,
  MCP error mapping.
- `tests/unit/ai/test_effective_config_parity.py` — one resolver: for identical resolver
  inputs, check, fix, review CLI, MCP and doctor resolve identical values and sources,
  and the two cap rules stay split (CLI/env may raise or lift; MCP's per-call argument
  only clamps).

## File-level resume (#2154 / ADR-0007)

Coverage is keyed `(path, normalized patch hash)` and stored in workflow artifacts (CI)
or `.lintro-cache/ai/review-state/` (local). A quiet re-review of an already-covered
HEAD makes zero provider calls. Partial coverage forces `ReviewVerdict.INCOMPLETE`;
`lintro review` still exits 0/1 from findings, and `classify_review_outcome.py` reddens
the check.

See [ADR-0007](../adr/0007-review-resume-and-artifact-state.md) and
[ai-review-report.md](../ai-review-report.md).
