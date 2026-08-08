# ADR-0006: AI effective-config and review execution architecture

## Status

Accepted

## Context

Lintro's AI subsystem already separates core from AI (#724), routes provider
calls through `call_ai`, and keeps review redaction as a mandatory choke point.
The remaining architectural risk is duplicated entry wiring and a concentrated
review orchestrator — not those foundations.

Today:

- `resolve_ai_config()` returns only `AIConfig`. Invocation overrides then apply
  independently via `apply_transport_override()` / `model_copy()`, and display
  code can reparse the raw `ai:` mapping.
- The `lintro review` CLI and the MCP `lintro_review` toolkit each perform
  largely the same preparation (resolve config, collect context, classify files,
  select checklist, optional lint digest, sensitivity, provider, `run_review`)
  with adapter-specific policy layered on top.
- `lintro/ai/review/orchestrator.py` owns sync/async boundary, session/budget
  lifetime, chunk planning, prompts/passes, response recovery, merge/filter, and
  metadata finalization in one module.
- Provider HTTP clients have no `close`/`aclose` yet (#1885); once that API
  exists, call-site ownership must still be decided (#1972 Phase 5).

Epic #1972 coordinates structural work around the existing AI backlog. It does
**not** absorb #1970 (env/CLI overrides + provenance), #1923 (transport
profiles), #1885 (provider-side `aclose`), or related issues. This ADR records
the cross-surface contracts those phases must share.

### Characterization coverage gap list (Phase 1)

Existing suites already pin large parts of review behavior
(`tests/unit/ai/review/test_orchestrator*.py`, `test_pipeline*.py`,
`test_prompt_builder.py`, `test_merge.py`, `test_error_contract.py`,
`test_errors_taxonomy.py`, `tests/unit/mcp/test_toolkit_review.py`,
`tests/unit/cli/test_review_command.py`). Phase 1 therefore adds only the gaps
below rather than re-testing those topics:

| Gap | Why it matters | Phase 1 lock |
| --- | --- | --- |
| AC10 core → AI import edge | `#724` boundary; `test_package_imports.py` only checks importability | `tests/unit/test_core_ai_import_boundary.py` |
| Shared preparation call set | CLI/MCP can drift before Phase 3 extracts `prepare_review` | preparation characterization tests |
| Effective-config seam parity | Both surfaces must resolve via `resolve_ai_config`; adapters then differ | config parity tests |
| Review exit 0 / 1 / 2 matrix | Exit 1 for successful P1 findings was under-locked vs exit 0/2 | exit semantics tests |
| Error-contract sharing | CLI JSON and MCP must keep one diagnosis shape | error-mapping characterization |
| MCP run-metadata key set | Agents depend on a stable `run` object | metadata key characterization |

Prompt golden fixtures, orchestrator phase isolation, and provider lifecycle
wiring are deferred to Phases 3–5 (and coordinated with #1884 / #1885).

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
- Support transport-scoped profiles (#1923) by extending the same pipeline, not
  a parallel override layer.
- Be consumed by execution, doctor/status, terminal review output, PR rendering,
  MCP, and advisory tools without reparsing the raw `ai:` mapping.
- Keep security-sensitive caps monotonic: an invocation may lower a configured
  cap where explicitly supported, never raise it (`ai.max_cost_usd` today).

#1970 implements the initial resolver. This epic must not introduce a second
resolver. Until #1970 lands, the seam remains `resolve_ai_config()` returning
`AIConfig`, with adapter-local overrides applied afterward.

### B. Shared review domain request and preparation

Introduce domain-level inputs/outputs (names illustrative) in a later phase:

```python
ReviewRunRequest
PreparedReview
prepare_review(...)
execute_review(...)
```

The shared layer owns deterministic preparation and review execution. Thin
adapters retain surface policy:

| Surface | Owns |
| --- | --- |
| CLI | Click validation/errors, progress UI, JSON/terminal rendering, GitHub posting, exit `0`/`1`/`2` |
| MCP | Argument envelope, workspace session/locking, budget clamp, structured tool errors, no posting |
| Advisory-only | Explicit master-switch semantics without implying the diff-review sub-toggle |

Do not hide adapter policy inside generic callbacks or create a plugin system.

### C. Decompose the orchestrator by phase

Preserve `run_review` / `run_review_async` as the stable facade. Move cohesive
internals into review-domain modules. Suggested boundaries (not mandatory
filenames):

- runner/session — sync/async boundary, provider and budget lifetime, progress
- planning/chunks — context-window and semantic chunk planning
- prompts/passes — built-in review passes and prompt assembly
- response pipeline — parsing, schema recovery, supplementary calls
- merge/filter — deduplication, severity/sensitivity policy, cross-pass merge
- metadata — final run metadata and provenance projection

All provider invocations continue through `call_ai`. Prompt redaction remains a
mandatory choke point. No prompt, finding, severity, or exit-code behavior may
change as part of file movement.

### D. Explicit provider/session ownership (after #1885)

#1885 owns the **provider-side API only** (`aclose()` on base + providers,
stale-loop client handling). Phase 5 of #1972 owns **all call-site wiring**:
which layer calls `aclose()`, exactly-once semantics on failure/cancellation,
and closing the 1+N providers in `custom_agent_runner.py`'s `provider_cache`.

The top-level AI run/session owns provider lifetime and closes it exactly once.
Do not add a competing lifecycle abstraction (for example a second
context-manager layer) before or beside #1885.

Providers are constructed at five sites today (`cli_utils/commands/review.py`,
`mcp/toolkits/review.py`, `tools/definitions/idiom_review.py`,
`ai/orchestrator.py`, `ai/liveness.py`). Closing inside the review orchestrator
would be a use-after-close hazard for MCP session reuse; ownership stays with
the constructing run/session.

### Exit semantics (unchanged)

| Exit | Meaning |
| --- | --- |
| `0` | Successful review with no P1 findings (or clean/below-threshold) |
| `1` | Successful review that produced P1 findings |
| `2` | No review produced (`REVIEW_ERROR_EXIT_CODE`) — provider/execution failure |

MCP maps the same failure taxonomy into structured envelopes rather than
process exit codes, but shares `build_error_contract` for diagnosis fields.

### Core → AI import boundary (AC10 / #724)

Core configuration and execution packages must not import AI internals. Allowed
direction is AI (and CLI/MCP adapters) importing core. Runtime import edges
from `lintro.config`, `lintro.models`, `lintro.enums`, `lintro.plugins`,
`lintro.parsers`, `lintro.formatters`, and core execution/output utilities into
`lintro.ai` are forbidden; `TYPE_CHECKING`-only annotations remain acceptable
where they do not load AI at import time.

## Consequences

- #1970 / #1923 / #1235 extend one resolver contract instead of inventing
  parallel override layers.
- Phase 3 can extract shared preparation behind characterization locks without
  changing adapter policy.
- Phase 4 can split the orchestrator behind `run_review` without changing
  product behavior.
- Phase 5 can wire `aclose()` at construction sites after #1885 without a
  competing lifecycle design.
- Characterization tests added in Phase 1 document the current seams; they must
  stay green across later phases unless an explicit product change is accepted.

## References

- Epic [#1972](https://github.com/lgtm-hq/py-lintro/issues/1972) — consolidate
  effective config and review execution architecture.
- [#1970](https://github.com/lgtm-hq/py-lintro/issues/1970) — env/CLI override
  layer and provenance (owns `ResolvedAIConfig` implementation).
- [#1923](https://github.com/lgtm-hq/py-lintro/issues/1923) — transport profiles
  must extend the same resolver.
- [#1885](https://github.com/lgtm-hq/py-lintro/issues/1885) — provider-side
  `aclose()` API only.
- [#724](https://github.com/lgtm-hq/py-lintro/issues/724) — core/AI import
  separation.
- `lintro/ai/interface.py` — `resolve_ai_config` seam.
- `lintro/cli_utils/commands/review.py` — CLI review adapter.
- `lintro/mcp/toolkits/review.py` — MCP review adapter.
- `lintro/ai/review/orchestrator.py` — stable `run_review` facade.
- `lintro/ai/review/error_contract.py` — shared failure diagnosis / exit `2`.
