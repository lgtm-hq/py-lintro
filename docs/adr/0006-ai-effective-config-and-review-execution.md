# ADR-0006: One effective AI config and one shared review execution path

## Status

Accepted

## Context

Epic [#1972](https://github.com/lgtm-hq/py-lintro/issues/1972) consolidates three
structural risks in the AI subsystem without changing product behaviour:

1. **Effective AI configuration is not yet a first-class contract.**
   `resolve_ai_config()` returns only `AIConfig`. Invocation overrides then apply via
   `apply_transport_override()` or `model_copy()`, and display code can reparse the raw
   `ai:` mapping. As env, CLI, project, future user config, transport profiles, and
   custom-agent model selection accumulate, effective values and provenance can drift
   between execution, diagnostics, and reporting.
2. **CLI and MCP duplicate review preparation.** Both resolve AI config and budget
   policy, collect context, classify files, select checklist items, optionally build a
   lint digest, resolve sensitivity / custom agents, create a provider, invoke
   `run_review`, and translate failures. Adapter policy legitimately differs (Click vs
   MCP envelopes, posting, budget clamp), but shared preparation can drift.
3. **The review orchestrator owns too many phases.** `run_review` remains the stable
   facade, but its internals combine session lifetime, chunk planning, prompts, response
   recovery, merge/filter, and metadata finalization.

Related work owns pieces of this without absorbing them here:

- [#1970](https://github.com/lgtm-hq/py-lintro/issues/1970) — env/CLI overrides and
  field provenance (consumes Decision A).
- [#1923](https://github.com/lgtm-hq/py-lintro/issues/1923) — transport-scoped profiles
  extend the same resolver.
- [#1885](https://github.com/lgtm-hq/py-lintro/issues/1885) — provider-side `aclose()`
  API only; Phase 5 of #1972 owns call-site wiring.
- [#724](https://github.com/lgtm-hq/py-lintro/issues/724) — core → AI import boundary
  (AC10).

## Decision

### A. One resolved-config value object

Establish a canonical result shaped like:

```python
@dataclass(frozen=True, slots=True)
class ResolvedAIConfig:
    config: AIConfig
    sources: Mapping[str, ConfigSource]
```

Exact naming is implementation-defined (#1970). The contract must:

- carry validated effective values and per-field provenance together;
- resolve precedence once per invocation:
  `flag > env > project > future user config > default`;
- allow transport profiles (#1923) to extend the same pipeline;
- distinguish an omitted value from an invalid override;
- be consumed by execution, doctor/status, terminal review output, PR rendering, MCP,
  and advisory tools without reparsing raw config;
- keep security-sensitive caps monotonic: an invocation may lower a configured cap where
  explicitly supported, never raise it.

This epic must not introduce a second resolver.

### B. Shared review domain request and preparation

Introduce domain-level inputs/outputs (names illustrative): `ReviewRunRequest`,
`PreparedReview`, `prepare_review(...)`, `execute_review(...)`.

The shared layer owns deterministic preparation and review execution. Thin adapters
retain surface policy:

| Surface | Owns |
| --- | --- |
| CLI | Click errors, progress UI, JSON/terminal rendering, GitHub posting, exit 0/1/2 |
| MCP | Argument envelope, workspace session/locking, budget clamp, structured tool errors, no posting |
| Advisory-only | Explicit master-switch semantics without implying the diff-review sub-toggle |

Do not hide adapter policy inside generic callbacks or create a plugin system.

### C. Decompose the orchestrator by phase

Move cohesive internals into review-domain modules while preserving `run_review` as the
stable facade. Suggested boundaries (not mandatory filenames): runner/session,
planning/chunks, prompts/passes, response pipeline, merge/filter, metadata.

All provider invocations continue through `call_ai`. Prompt redaction remains a
mandatory choke point. No prompt, finding, severity, or exit-code behaviour may change
as part of file movement.

### D. Explicit provider/session ownership

Coordinate with #1885. Once providers support `close`/`aclose`, the top-level AI
run/session owns provider lifetime and closes it exactly once, including failure paths
and custom-agent model overrides. #1885 owns the provider-side API; Phase 5 of #1972
owns all call-site wiring (including `custom_agent_runner` provider cache). Do not add a
competing lifecycle abstraction before #1885 lands.

### Ownership boundaries (summary)

| Concern | Owner |
| --- | --- |
| Effective `AIConfig` + provenance | Single resolver (Decision A; #1970 implements) |
| Transport profiles / cost basis | Same resolver (#1923) |
| Shared review prep / execution | Review domain (Decision B; Phase 3) |
| CLI / MCP / advisory surface policy | Adapters only |
| Orchestrator phase modules | Behind `run_review` (Decision C; Phase 4) |
| Provider `aclose` API | #1885 |
| Provider close call-sites | Phase 5 of #1972 |
| Core → AI import edge | #724 boundary; AC10 guard tests |

### Exit semantics (unchanged)

- `0` — successful clean / below-threshold review
- `1` — successful review with P1 findings
- `2` — no review produced (`REVIEW_ERROR_EXIT_CODE`)

### Characterization inventory (Phase 1 gap list)

Existing coverage already pins large parts of review behaviour
(`tests/unit/ai/review/test_orchestrator*.py`, `test_pipeline*.py`,
`test_prompt_builder.py`, `test_merge.py`, `test_error_contract.py`,
`test_errors_taxonomy.py`, `tests/unit/mcp/test_toolkit_review.py`,
`tests/unit/cli/test_review_command.py`). Phase 1 does **not** re-test those topics
wholesale. The gaps that lacked golden coverage and are pinned by the Phase 1 suite:

| Gap | Why it mattered | Characterization location |
| --- | --- | --- |
| AC10 core → AI import edge | `test_package_imports.py` only checks importability; #724 boundary not enforced for config/models/parsers/enums/formatters/plugins | `tests/unit/test_core_ai_import_boundary.py` |
| CLI exit `1` on P1 findings | CLI tests covered exit `0` (no P1) and exit `2` (no review); not exit `1` | `tests/unit/ai/review/test_architecture_characterization_1972.py` |
| Effective-config parity | CLI and MCP both call `resolve_ai_config`, but nothing asserted identical resolution from the same raw `ai:` mapping | same |
| CLI/MCP prep kwargs parity | Shared `run_review` inputs can drift independently | same |
| CLI/MCP error-contract body parity | Each surface tested alone; shared `build_error_contract` body not asserted across CLI JSON and MCP `review_error` detail | same |
| Review metadata projection | MCP serializes a subset of `ReviewMetadata`; field set not frozen as a golden | same |

## Consequences

- Downstream issues (#1970, #1923, Phase 2–5) extend one documented contract rather than
  inventing parallel seams.
- Characterization tests freeze today's behaviour so Phase 3/4 refactors stay
  behaviour-preserving.
- Product behaviour (prompts, findings, severities, exits) is intentionally unchanged by
  this ADR alone.

## References

- Epic [#1972](https://github.com/lgtm-hq/py-lintro/issues/1972)
- `lintro/ai/interface.py` — `resolve_ai_config`
- `lintro/cli_utils/commands/review.py` — CLI adapter
- `lintro/mcp/toolkits/review.py` — MCP adapter
- `lintro/ai/review/orchestrator.py` — `run_review` facade
- `lintro/ai/review/error_contract.py` — exit `2` / error envelope
- Issue [#724](https://github.com/lgtm-hq/py-lintro/issues/724) — core/AI boundary
