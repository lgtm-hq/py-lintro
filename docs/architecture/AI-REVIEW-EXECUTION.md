# AI Effective Config and Review Execution

This page is the architecture index for Lintro's AI review execution seams. The
normative decision record is
[ADR-0006](../adr/0006-ai-effective-config-and-review-execution.md) (epic
[#1972](https://github.com/lgtm-hq/py-lintro/issues/1972)).

## Ownership boundaries

| Concern                                             | Owner today                                             | Target                                         |
| --------------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------- |
| Typed `AIConfig` from raw `ai:` mapping             | `AIConfig.resolve_from_mapping()` → `ResolvedAIConfig`  | Same resolver; #1923 extends it                |
| Invocation transport / timeout / cost-cap overrides | CLI review: `apply_cli_overrides` on `ResolvedAIConfig` | Same resolver pipeline (#1970 / #1923 / #2024) |
| Monotonic cost-cap clamp                            | MCP adapter (`resolve_budget_policy`)                   | Shared domain prep; adapters keep policy       |
| Diff review preparation                             | Duplicated in CLI + MCP                                 | `prepare_review` / `execute_review` (Phase 3)  |
| Review execution facade                             | `run_review` / `run_review_async`                       | Unchanged facade; internals split (Phase 4)    |
| Provider client `aclose()` API                      | Not yet (#1885)                                         | Provider-side only in #1885                    |
| Provider close call-site wiring                     | N/A until #1885                                         | Phase 5 of #1972                               |

## Shared preparation (current duplicated steps)

Both the CLI (`lintro review`) and MCP (`lintro_review`) currently:

1. Resolve AI config. CLI review uses `AIConfig.resolve_from_mapping` plus
   `apply_cli_overrides` (keeps provenance). MCP uses `resolve_ai_config`, which unwraps
   the same env-aware parse.
2. Gate on `ai.review` being enabled (adapter-specific error shape).
3. Collect review context, classify changed files, select/format checklist items.
4. Optionally build a lint digest.
5. Resolve sensitivity policy and construct a provider.
6. Invoke `run_review` and translate failures.

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
- `tests/unit/ai/review/test_architecture_characterization_1972.py` — gap coverage:
  config-resolution idempotence, shared `run_review` kwargs, error-contract body parity,
  MCP error mapping.
