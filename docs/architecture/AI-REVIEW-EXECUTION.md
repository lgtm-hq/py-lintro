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
   agents from the request's `CustomAgentMode`.
4. Each adapter constructs its own provider — provider lifetime stays with the
   constructing surface until #1972 Phase 5 — and calls `execute_review`, which is the
   single `run_review` call site.
5. Each adapter translates failures into its own contract.

`ReviewExecutionPolicy` carries what is genuinely adapter-only into `execute_review`:
terminal progress, `--context-window`, resume state, `--full`, and the CLI's cost-cap
gate. MCP runs on `DEFAULT_EXECUTION_POLICY`, whose values are `run_review`'s own
defaults. There are no callbacks and no hooks: the policy is a value.

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
- `tests/unit/ai/review/test_cli_mcp_parity.py` — CLI/MCP parity: the two surfaces
  produce an **equal** `PreparedReview` for one workspace, and the only divergence left
  is the named `ReviewExecutionPolicy` allowlist.
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
