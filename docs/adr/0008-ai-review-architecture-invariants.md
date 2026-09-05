# ADR-0008: AI review architecture invariants

## Status

Accepted

## Context

`lintro/ai/review/orchestrator.py` currently owns the sync/async boundary, session and
budget lifetime, chunk planning, prompt assembly, the built-in and custom-agent passes,
response parsing and recovery, finding merge and filter policy, and metadata
finalisation. Epic [#1972](https://github.com/lgtm-hq/py-lintro/issues/1972) decomposes
it by phase without changing product behaviour, and the roadmap
([#2288](https://github.com/lgtm-hq/py-lintro/issues/2288), track A) schedules that work
as #2299-#2302.

A behaviour-preserving decomposition needs two things written down before it starts:
which behaviours are pinned by tests, and which structural properties may not change no
matter how the files are rearranged. The first is the golden suite added by
[#2298](https://github.com/lgtm-hq/py-lintro/issues/2298) under
`tests/unit/ai/review/golden/`. The second is this ADR.

The invariants below are not new decisions. Each already holds in the code on `main`;
recording them here makes them reviewable as a checklist rather than rediscoverable by
reading a 3,000-line module.

## Decision

Lintro's AI review subsystem holds these invariants across every refactor in #1972.

**1. `run_review` is the facade.** Adapters call
`lintro.ai.review.orchestrator.run_review` and nothing deeper. It is the one sync/async
boundary: `asyncio.run` is entered exactly once so a single event loop, and therefore a
single provider client, serves a whole review. Moving phases into new modules may not
add a second public entry point for review execution.

**2. Every provider call goes through `call_ai`.** `lintro.ai.invoke.call_ai` is the
sole dispatch point for retries, fallback, budget accounting, and transport selection.
No review module may call a provider client directly. This is also the test seam: the
golden suite replays fixed responses by patching `call_ai` and patches nothing below it,
so a provider client added or changed underneath cannot invalidate the goldens.

**3. Redaction is a choke point, and it wins.** All prompt text destined for a provider
passes through `lintro.ai.review.prompt_redaction.redact_prompt_text`. The git-native
prompt builder embeds a redacted diff by default rather than delegating `git diff` to
the provider; the delegated path exists only behind the explicit
`review_allow_unredacted_git_native` opt-out. A refactor may not introduce a prompt path
that bypasses this function.

**4. Core never imports `lintro.ai`.** The `core-does-not-import-ai` contract in
`[tool.importlinter]` enforces the
[#724](https://github.com/lgtm-hq/py-lintro/issues/724) boundary: nothing outside
`lintro.ai`, `lintro.mcp`, `lintro.cli_utils` and `lintro.api` may import `lintro.ai`.
That guard is owned by [#2290](https://github.com/lgtm-hq/py-lintro/issues/2290), not by
the review suite, and is named here so the review decomposition does not reinvent it.

**5. Provider lifetime belongs to the run, not the adapter.** Today CLI and MCP each
build their own provider and neither closes it; Phase 5
([#2302](https://github.com/lgtm-hq/py-lintro/issues/2302)) moves ownership into the run
session so `aclose()` is called exactly once, including on failure and cancellation and
including the per-agent providers in `custom_agent_runner.py`. Until then, no second
lifecycle abstraction may be added.

**6. Config is resolved once per invocation.** `resolve_ai_config` is the single
resolver; post-resolution ad hoc override paths are debt that Phase 2
([#2299](https://github.com/lgtm-hq/py-lintro/issues/2299)) removes rather than a
pattern to copy. Security-relevant caps stay monotonic: an invocation may lower a
configured cap, never raise it.

**7. Behaviour-preserving means byte-identical goldens.** Prompt bytes, finding order
and severity, checklist merge precedence, merged-result shape, run metadata fields, and
the CLI exit codes 0/1/2 are pinned by tests. A refactor PR that changes a golden is not
behaviour-preserving: it must say which golden changed and why, and the change needs
owner approval before merge (roadmap #2288, execution protocol item 5).

## Consequences

- The decomposition has an objective pass/fail signal.
  `uv run pytest tests/unit/ai/review/golden` failing means product behaviour moved, not
  that a file was renamed.
- Goldens are plain files compared as strings. There is no snapshot library and no
  auto-approval; regenerating requires `LINTRO_UPDATE_GOLDENS=1`, which shows up in the
  diff as a deliberate act.
- Some values cannot be pinned: the prompt boundary marker is random by design
  (`lintro/ai/sanitize.py`) and the run timestamp, duration and phase timings are
  wall-clock. The suite pins the marker with a patch and replaces the timing fields with
  sentinels, then asserts their types separately so sentinelling never becomes a way to
  stop characterising a field.
- The goldens are provider-agnostic: they use a stand-in provider identity and an
  explicit `context_window_override`, so no golden encodes a default provider, model, or
  pricing-table entry. Lintro has no default provider and these files must not imply
  one.
- The fixture is one `ReviewContext` with three changed files (a text modification, a
  binary file, and a rename). Behaviours that need a fourth shape need a second fixture;
  growing this one invalidates every golden at once.

## References

- `lintro/ai/review/orchestrator.py` — `run_review`, `build_review_prompt`,
  `build_git_native_review_prompt`, `merge_review_results`.
- `lintro/ai/invoke.py` — `call_ai`.
- `lintro/ai/review/prompt_redaction.py` — the redaction choke point.
- `tests/unit/ai/review/golden/` — the golden suite and its fixture.
- `tests/unit/ai/review/test_cli_mcp_parity.py` — CLI/MCP preparation parity.
- [ADR-0006](0006-ai-effective-config-and-review-execution.md) — effective AI config and
  the shared review path.
- Issues [#1972](https://github.com/lgtm-hq/py-lintro/issues/1972),
  [#2288](https://github.com/lgtm-hq/py-lintro/issues/2288),
  [#2298](https://github.com/lgtm-hq/py-lintro/issues/2298).
