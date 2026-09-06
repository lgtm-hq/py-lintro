# ADR-0006: AI effective-config and review execution architecture

## Status

Accepted

## Context

Lintro's AI subsystem already separates core from AI (#724), routes provider calls
through `call_ai`, and keeps review redaction as a mandatory choke point. The remaining
architectural risk is duplicated entry wiring and a concentrated review orchestrator —
not those foundations.

Today:

- `resolve_ai_config()` returned only `AIConfig`. Invocation overrides then applied
  independently via a post-resolution `model_copy()`, and display code could reparse the
  raw `ai:` mapping. [#2299](https://github.com/lgtm-hq/py-lintro/issues/2299) removed
  both: `lintro.ai.effective_config.resolve_effective_ai_config` is now the one
  resolver, and it is the only production caller of `AIConfig.resolve_from_mapping`.
- The `lintro review` CLI and the MCP `lintro_review` toolkit each perform largely the
  same preparation (resolve config, collect context, classify files, select checklist,
  optional lint digest, sensitivity, provider, `run_review`) with adapter-specific
  policy layered on top.
- `lintro/ai/review/orchestrator.py` owns sync/async boundary, session/budget lifetime,
  chunk planning, prompts/passes, response recovery, merge/filter, and metadata
  finalization in one module.
- Provider HTTP clients have no `close`/`aclose` yet (#1885); once that API exists,
  call-site ownership must still be decided (#1972 Phase 5).

Epic #1972 coordinates structural work around the existing AI backlog. It does **not**
absorb #1970 (env/CLI overrides + provenance), #1923 (transport profiles), #1885
(provider-side `aclose`), or related issues. This ADR records the cross-surface
contracts those phases must share.

### Characterization coverage gap list (Phase 1)

Existing suites already pin large parts of review behavior
(`tests/unit/ai/review/test_orchestrator*.py`, `test_pipeline*.py`,
`test_prompt_builder.py`, `test_merge.py`, `test_error_contract.py`,
`test_errors_taxonomy.py`, `tests/unit/mcp/test_toolkit_review.py`,
`tests/unit/cli/test_review_command.py`). Phase 1 therefore adds only the gaps below
rather than re-testing those topics:

| Gap                          | Why it matters                                                                                             | Phase 1 lock                                 |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| AC10 core → AI import edge   | `#724` boundary; `test_package_imports.py` only checks importability                                       | `tests/unit/test_core_ai_import_boundary.py` |
| Shared preparation call set  | CLI/MCP can drift before Phase 3 extracts `prepare_review`                                                 | preparation characterization tests           |
| Effective-config seam parity | MCP via `resolve_ai_config`; CLI review via `resolve_from_mapping` + CLI overlay (#1970); unified by #2299 | config parity tests                          |
| Review exit 0 / 1 / 2 matrix | Exit 1 for successful P1 findings was under-locked vs exit 0/2                                             | exit semantics tests                         |
| Error-contract sharing       | CLI JSON and MCP must keep one diagnosis shape                                                             | error-mapping characterization               |
| MCP run-metadata key set     | Agents depend on a stable `run` object                                                                     | metadata key characterization                |

Prompt golden fixtures landed in Phase 1 after all —
[ADR-0008](0008-ai-review-architecture-invariants.md) records the invariants and #2298
adds the suite under `tests/unit/ai/review/golden/`. Orchestrator phase isolation and
provider lifecycle wiring remain deferred to Phases 3–5, coordinated with
[#1884](https://github.com/lgtm-hq/py-lintro/issues/1884) and
[#1885](https://github.com/lgtm-hq/py-lintro/issues/1885).

## Decision

### A. One resolved-config value object

Lintro will treat effective AI configuration as a single resolved value object
(illustrative name; #1970 owns the concrete type):

```python
@dataclass(frozen=True, slots=True)
class ResolvedAIConfig:
    config: AIConfig
    sources: Mapping[str, ConfigSource]
```

Contract invariants:

- Carry validated effective values and per-field provenance together.
- Resolve precedence once per invocation:
  `flag > env > project > future user config (#1235) > default`.
- Distinguish omitted values from invalid overrides; invalid overrides fail at
  resolution and never silently fall through.
- Support transport-scoped profiles (#1923) by extending the same pipeline, not a
  parallel override layer.
- Be consumed by execution, doctor/status, terminal review output, PR rendering, MCP,
  and advisory tools without reparsing the raw `ai:` mapping.
- CLI and env overlays may raise or lift `ai.max_cost_usd` (`LINTRO_AI_MAX_COST_USD` /
  `lintro review --max-cost-usd`; overlay `uncapped` lifts the ceiling; overlay `0` is
  rejected as ambiguous). Overlays beat transport profile caps as well as the legacy
  scalar; YAML `0` remains a $0 cap. MCP's per-call `max_cost_usd` argument remains a
  monotonic clamp: it may lower the effective ceiling, never raise it. #2024 originally
  mapped overlay `0` to uncapped; #2154 / ADR-0007 superseded that.

Issue #1970 implemented the initial parse (`AIConfig.resolve_from_mapping` returning
`ResolvedAIConfig`); #2299 put one function in front of it,
`lintro.ai.effective_config.resolve_effective_ai_config(mapping, *, cli_overrides, diagnostics)`.
This epic must not introduce a second resolver. CLI review passes
`--provider`/`--model`/`--transport`/`--max-cost-usd` as `AICliOverrides`; the `check`
CLI and the lint API pass `--transport` the same way, and `fmt` uses the same resolver
with no flag of its own; other surfaces pass none and consume the same value, with
`resolve_ai_config()` as the values-only unwrap.

### B. Shared review domain request and preparation

Domain-level inputs/outputs, landed by
[#2300](https://github.com/lgtm-hq/py-lintro/issues/2300) in
`lintro/ai/review/preparation.py`:

```python
ReviewRunRequest              # typed inputs, built by each adapter
prepare_review(request, *, resolved) -> PreparedReview   # deterministic, no provider
execute_review(prepared, *, provider, policy) -> ReviewResult
```

`prepare_review` is provider-free: it applies the run's timeout and transport profile,
collects the diff context, classifies files, selects and formats the checklist, builds
the optional lint digest, resolves sensitivity, and resolves custom agents. Two adapters
that build equal requests over one workspace must produce **equal** `PreparedReview`
values; `tests/unit/ai/review/test_cli_mcp_parity.py` asserts that equality.

`ReviewExecutionPolicy` carries the remaining adapter-only knobs (progress,
`--context-window`, resume state, `--full`, the CLI cost-cap gate) into `execute_review`
as a value — not a callback, and not a hook. MCP runs on the default policy, whose
values are `run_review`'s own defaults. MCP's `max_cost_usd` clamp is applied to the
prepared review (`PreparedReview.with_max_cost_usd`) after preparation, keeping the
clamp monotonic and adapter-owned.

The shared layer owns deterministic preparation and review execution. Thin adapters
retain surface policy:

| Surface       | Owns                                                                                            |
| ------------- | ----------------------------------------------------------------------------------------------- |
| CLI           | Click validation/errors, progress UI, JSON/terminal rendering, GitHub posting, exit `0`/`1`/`2` |
| MCP           | Argument envelope, workspace session/locking, budget clamp, structured tool errors, no posting  |
| Advisory-only | Explicit master-switch semantics without implying the diff-review sub-toggle                    |

Do not hide adapter policy inside generic callbacks or create a plugin system.

### C. Decompose the orchestrator by phase

Preserve `run_review` / `run_review_async` as the stable facade. Move cohesive internals
into review-domain modules. Suggested boundaries (not mandatory filenames):

- runner/session — sync/async boundary, provider and budget lifetime, progress
- planning/chunks — context-window and semantic chunk planning
- prompts/passes — built-in review passes and prompt assembly
- response pipeline — parsing, schema recovery, supplementary calls
- merge/filter — deduplication, severity/sensitivity policy, cross-pass merge
- metadata — final run metadata and provenance projection

All provider invocations continue through `call_ai`. Prompt redaction remains a
mandatory choke point. No prompt, finding, severity, or exit-code behavior may change as
part of file movement.

### D. Explicit provider/session ownership (after #1885)

Issue #1885 owns the **provider-side API only** (`aclose()` on base + providers,
stale-loop client handling). Phase 5 of #1972 owns **all call-site wiring**: which layer
calls `aclose()`, exactly-once semantics on failure/cancellation, and closing the 1+N
providers in `custom_agent_runner.py`'s `provider_cache`.

The top-level AI run/session owns provider lifetime and closes it exactly once. Do not
add a competing lifecycle abstraction (for example a second context-manager layer)
before or beside #1885.

Providers are constructed at five sites today (`cli_utils/commands/review.py`,
`mcp/toolkits/review.py`, `tools/definitions/idiom_review.py`, `ai/orchestrator.py`,
`ai/liveness.py`). Closing inside the review orchestrator would be a use-after-close
hazard for MCP session reuse; ownership stays with the constructing run/session.

### Exit semantics (unchanged)

| Exit | Meaning                                                                    |
| ---- | -------------------------------------------------------------------------- |
| `0`  | Successful review with no P1 findings (or clean/below-threshold)           |
| `1`  | Successful review that produced P1 findings                                |
| `2`  | No review produced (`REVIEW_ERROR_EXIT_CODE`) — provider/execution failure |

MCP maps the same failure taxonomy into structured envelopes rather than process exit
codes, but shares `build_error_contract` for diagnosis fields.

### Core → AI import boundary (AC10 / #724)

Core configuration and execution packages must not import AI internals. Allowed
direction is AI (and CLI/MCP adapters) importing core. Runtime import edges from
`lintro.config`, `lintro.models`, `lintro.enums`, `lintro.plugins`, `lintro.parsers`,
`lintro.formatters`, and core execution/output utilities into `lintro.ai` are forbidden;
`TYPE_CHECKING`-only annotations remain acceptable where they do not load AI at import
time.

## Consequences

### Migration (#2299)

Three internal entry points were removed when the resolver was unified. None was part of
the public `lintro` API, so this is not a breaking change for CLI or `lintro.api`
consumers; code reaching into `lintro.ai` internals moves as follows:

| Removed                                                | Use instead                                                                       |
| ------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `AIConfig.from_mapping(data)`                          | `resolve_effective_ai_config(data).config`                                        |
| `lintro.ai.transport.apply_transport_override(cfg, t)` | `resolve_effective_ai_config(mapping, cli_overrides=AICliOverrides(transport=t))` |
| `lintro.ai.transport.apply_cli_overrides` (re-export)  | `lintro.ai.config_overrides.apply_cli_overrides`, or the resolver                 |

`AIConfig.resolve_from_mapping` still exists but is the project + environment half only;
surfaces call `resolve_effective_ai_config` rather than reaching past it.

- #1970 / #1923 / #1235 extend one resolver contract instead of inventing parallel
  override layers.
- Phase 3 extracted shared preparation behind the characterization locks without
  changing adapter policy (#2300). One deliberate behaviour change came with it:
  `ai.exclude_paths` now shapes the MCP review's context as well as the CLI's, because
  preparation reads the exclusion from the resolved AI config. That was the single
  context axis the two surfaces disagreed on, and it closed in the CLI's direction.
- Phase 4 can split the orchestrator behind `run_review` without changing product
  behavior.
- Phase 5 can wire `aclose()` at construction sites after #1885 without a competing
  lifecycle design.
- Characterization tests added in Phase 1 document the current seams; they must stay
  green across later phases unless an explicit product change is accepted.

## References

- Epic [#1972](https://github.com/lgtm-hq/py-lintro/issues/1972) — consolidate effective
  config and review execution architecture.
- [#1970](https://github.com/lgtm-hq/py-lintro/issues/1970) — env/CLI override layer and
  provenance (owns `ResolvedAIConfig` implementation).
- [#2024](https://github.com/lgtm-hq/py-lintro/issues/2024) — cost-cap overlay
  (`LINTRO_AI_MAX_COST_USD` / `--max-cost-usd`). Overturns the #1970 non-goal that
  forbade raising `ai.max_cost_usd`. Overlay `0` = uncapped here is superseded by
  [#2154](https://github.com/lgtm-hq/py-lintro/issues/2154) / ADR-0007 (`uncapped`
  sentinel; overlay `0` rejected).
- [#1923](https://github.com/lgtm-hq/py-lintro/issues/1923) — transport profiles must
  extend the same resolver.
- [#1885](https://github.com/lgtm-hq/py-lintro/issues/1885) — provider-side `aclose()`
  API only.
- [#724](https://github.com/lgtm-hq/py-lintro/issues/724) — core/AI import separation.
- `lintro/ai/interface.py` — `resolve_ai_config` seam.
- `lintro/ai/review/preparation.py` — shared review request, preparation, and execution
  (#2300).
- `lintro/cli_utils/commands/review.py` — CLI review adapter.
- `lintro/mcp/toolkits/review.py` — MCP review adapter.
- `lintro/ai/review/orchestrator.py` — stable `run_review` facade.
- `lintro/ai/review/error_contract.py` — shared failure diagnosis / exit `2`.
