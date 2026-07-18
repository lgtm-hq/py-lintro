# Idiom Review Tool Analysis

## Overview

`idiom-review` is a first-class `ToolDefinition` plugin that uses the configured AI
provider to find issues that syntax-matching linters structurally cannot. It is
**distinct from the `lintro review` diff-review command** — it participates in the
normal `lintro check` pipeline like any other tool and produces standard `ToolResult` /
`Issue` objects.

It has no external binary; all work is done through the existing AI provider
abstraction, respecting the same retry, fallback, and cost-budget controls used by other
AI features.

## Core Tool Capabilities

Unlike conventional linters, `idiom-review` has no upstream CLI equivalent — it is
native to Lintro. Its capabilities are:

- **Two analysis modes:**
  - **`per-file`** (Mode 1) — finds idiomatic misses per file: correct but verbose code
    (e.g. `found = False; for x in items: ...` instead of `any(cond for x in items)`).
  - **`duplication`** (Mode 2) — finds the same utility logic reimplemented across
    multiple files, invisible to per-file linters, with a suggested extraction point.
  - **`both`** — runs both modes in a single pass.
- **Opt-in gate** — disabled by default (`enabled: false`); a no-op until explicitly
  opted in.
- **Content-hash caching** — findings cached under `.lintro-cache/idiom`; unchanged
  files cost no API calls on repeat runs.
- **Cost bound** — `max_files` caps the number of files reviewed per run.
- **Confidence filter** — `min_confidence` drops low-quality findings before reporting.
- **Language filter** — optional `language` option restricts the review scope.
- **Graceful degradation** — when no AI provider is available (missing SDK, API key, or
  exhausted credits), the tool produces a skipped result rather than failing the run.

## Lintro Implementation Analysis

### ✅ Preserved / Implemented Features

- ✅ Integrates with the standard `ToolDefinition` plugin interface — `check()` returns
  a `ToolResult` with structured `Issue` objects
- ✅ Uses the shared AI provider abstraction (retry, backoff, timeout, cost display)
- ✅ Respects `ai.enabled`, `ai.provider`, `ai.model`, and `ai.api_key_env` from the
  top-level AI config
- ✅ Content-hash caching avoids redundant API calls for unchanged files
- ✅ `max_files` and `min_confidence` options provide cost and quality controls
- ✅ `mode` selects per-file, duplication, or both analysis strategies
- ✅ Graceful skip when provider is unavailable — run still passes

### ⚠️ Limitations / Notes

- ⚠️ **No auto-fix** — AI findings are reported as issues; fixes are not applied
  automatically (use `--fix` for AI-assisted fix suggestions on other tools)
- ⚠️ **No external binary** — cannot be run outside of Lintro; depends entirely on the
  configured AI provider
- ⚠️ **Non-deterministic** — AI responses may vary across runs for the same input;
  caching mitigates this for unchanged files
- ⚠️ **API cost** — each uncached file incurs one or more API calls; use `max_files` and
  caching to bound costs in large repos

### 🚀 Enhancements vs. a hypothetical standalone tool

- Unified `ToolResult` / `Issue` output compatible with all Lintro output formats (grid,
  JSON, HTML, CSV, Markdown)
- Participates in `--diff` scoping — only files changed vs. the base ref are reviewed
  when `--diff` is active
- No external binary to install or update; versioned with Lintro itself

## Usage Comparison

### Direct AI call (no Lintro)

Without Lintro, reproducing this analysis would require writing custom prompt
orchestration, caching, retry logic, and result parsing.

### Lintro wrapper

```bash
# Ad-hoc run (opt-in via CLI, no config change needed)
lintro chk --tools idiom-review --tool-options idiom-review:enabled=true

# Persistent opt-in via config
# .lintro-config.yaml:
# tools:
#   idiom-review:
#     options:
#       enabled: true
#       mode: per-file
#       min_confidence: medium
#       max_files: 25
lintro chk --tools idiom-review

# Duplication mode across the whole repo
lintro chk --tools idiom-review \
  --tool-options idiom-review:enabled=true,idiom-review:mode=duplication
```

## Configuration Strategy

- Requires `ai.enabled: true` and a valid API key in the environment before any analysis
  runs.
- Tool-level `enabled` option (default `false`) acts as a second opt-in gate so that
  `lintro chk` does not silently incur API costs.
- `max_files` (default 25) is the primary cost-control knob for large repos.
- `min_confidence` (default `medium`) filters noisy low-confidence findings.
- Caching is automatic; clear `.lintro-cache/idiom` to force a full re-analysis.

See [AI Configuration](../configuration.md#idiom-review-tool-idiom-review) and
[AI Features Guide](../ai-features.md#ai-idiom-review-idiom-review-tool) for full
configuration reference.

## Recommendations

- Enable `idiom-review` selectively in CI on changed files only (combine with `--diff`)
  to keep API costs bounded.
- Start with `mode: per-file` and a low `max_files` limit to evaluate finding quality
  before enabling `duplication` mode on large repos.
- Use `min_confidence: high` in automated pipelines to reduce false positives; reserve
  `medium` for interactive developer runs where human review filters noise.
- Do not rely on `idiom-review` as a replacement for conventional linters — it
  complements them by surfacing structural patterns those linters cannot detect.
