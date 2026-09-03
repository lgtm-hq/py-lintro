# AI-Powered Features

Lintro includes optional AI-powered features that provide actionable insights and
interactive fix suggestions on top of standard linting results.

> **Requirements:** Python package extra + an API key.
>
> ```bash
> pip install -e '.[ai]'
> export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY for OpenAI
> ```

## Quick Start

```bash
# Enable AI in your config
# .lintro-config.yaml
# ai:
#   enabled: true
#   provider: anthropic

# Run check — AI summary is generated automatically (1 API call)
lintro check

# Add interactive fix suggestions
lintro check --fix

# Auto-fix with AI post-fix summary
lintro format
```

## Features Overview

### AI Summary (default with `check` / `chk`)

When AI is enabled, every `lintro check` run generates a single-call AI summary that
provides:

- **Overview** — high-level assessment of code quality
- **Key patterns** — systemic issues, not individual occurrences
- **Priority actions** — ordered by impact (fixes that resolve the most issues first)
- **Estimated effort** — rough time estimate to address all issues

This costs one API call regardless of how many issues exist.

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI SUMMARY — actionable insights
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Code quality is generally good but 12 type annotation issues
  in src/utils/ suggest a systematic gap in utility functions.

  Key Patterns:
  • Missing type annotations in 8 utility functions in src/utils/
  • Unused imports in 4 test files (likely copy-paste artifacts)

  Priority Actions:
  1. Add return type annotations to src/utils/ (resolves 8 issues)
  2. Remove unused imports in test files (resolves 4 issues)

  Estimated effort: 20-30 minutes of focused cleanup
```

### Interactive Fix Suggestions (`--fix`)

The `--fix` flag generates AI-powered code diffs and presents them for interactive
review:

```bash
lintro check --fix
```

For each group of issues, you're prompted:

```text
[y]accept group / [a]accept group + remaining / [r]reject / [d]iffs / [s]kip / [v]validate-after-group / [q]quit
```

- **Accept group** — applies only the current group
- **Accept group + remaining** — applies current group, then auto-accepts the rest
- **Reject** — skips this group
- **Diffs** — shows the unified diff before deciding
- **Skip** — moves to the next group
- **Validate-after-group** — toggles immediate tool validation after each accepted group
  (does not accept/apply fixes by itself)
- **Quit** — stops the review

Each group now includes:

- **Risk label** — `safe-style` vs `behavioral-risk` (classified by the AI model)
- **Patch stats** — files touched, `+/-` lines, and hunk count

Risk classification is AI-driven: the model self-reports whether each fix is purely
cosmetic (`safe-style`) or affects behavior (`behavioral-risk`). Unknown or empty
classifications default to `behavioral-risk` for safety.

For safe-style groups, pressing `Enter` defaults to accepting the group.

After the review session, a post-fix AI summary contextualizes what was fixed and what
remains.

### Git Checkpoints & Rollback

Before an AI fix batch mutates files, lintro captures a snapshot under
`refs/lintro/checkpoints/<run-id>` using git plumbing on a temporary index
(`GIT_INDEX_FILE`). This does **not** touch your index, stash, or `HEAD`.

- **Rollback** restores only the files lintro targeted from that checkpoint tree. Every
  blob is read before anything is written, each file is replaced atomically and keeps
  its mode, and paths that were not part of the snapshot are never touched.
- **Diff** against the checkpoint shows how the targeted files differ from their
  pre-batch state. When a run changed anything, lintro prints the ref so you can run
  `git diff <ref>` or `git restore --source=<ref> --worktree -- <path>` after it exits.
  `restore --worktree` is deliberate: `git checkout <ref> -- <path>` would also rewrite
  your index. This is a diff of the _files_, not an audit trail of lintro alone — an
  edit you made to a targeted file after capture appears in it too.
- **Interactive reject** restores rejected files from the checkpoint tree, not from
  in-memory copies. Files an earlier accepted group already changed are left alone, so
  rejecting one group never discards fixes you just accepted.
- **Retention** keeps the last N checkpoint refs (default 10), pruning older ones via
  `git update-ref -d` before the new ref is written. `0` keeps only the current run's
  checkpoint.
- **Fallback:** outside a git work tree (or in a bare repo), lintro falls back to
  file-content snapshots / the legacy reverse patch under `.lintro-cache/ai`.

Optional: set `ai.checkpoint_fmt: true` to capture the same style of checkpoint before
`lintro format` mutates files. This one is git-only — an in-process snapshot would not
outlive the run, so nothing is captured outside a git work tree.

> **Semantics:** Rolling back a lintro target overwrites that file with the pre-batch
> snapshot — including any edits you made to that same file between capture and
> rollback. Non-target files and your staged/unstaged index state are left alone.

### AI Fixes in `format`

When running `lintro format`, tools auto-fix what they can. For remaining unfixable
issues, the AI generates fix suggestions and presents them interactively (same UX as
`--fix` in `check`). After the session, a post-fix summary wraps up what was
accomplished.

### Advisory AI finders (`idiom-review`)

Unlike the AI summary and `--fix` flows (where AI _explains_ or _fixes_ issues that
linters found), an **AI finder** uses AI to _find_ issues that syntax-matching linters
structurally cannot. `idiom-review` is the first one. It has no external binary — it
runs through the existing AI provider abstraction, respecting the same retry, fallback,
and cost-budget controls.

> **Migration (#1308).** AI finders used to run under `lintro chk`. They now run under
> `lintro review` only. Every tool declares an execution class — `deterministic` (all
> classic linters) or `advisory` (AI finders) — and `chk` runs deterministic tools
> exclusively. Running `lintro chk --tools idiom-review` is now an error that points at
> the review verb.
>
> ```bash
> # before
> lintro chk --tools idiom-review
>
> # now (both forms still require the tool's own `enabled` opt-in, from
> # `tools.idiom-review` config or `--tool-options`)
> lintro review --advisory-only --advisory-tools idiom-review
> ```
>
> Why: advisory findings are opinions produced by a nondeterministic model. Letting them
> share `chk` meant two identical runs could disagree, the `--fail-under` health-score
> gate could move on model mood rather than on regressions, and every contributor paid
> API latency and dollars on a command meant to be reflexive and offline. Your
> `tools.idiom-review` config section is unchanged — only the invoking verb moved.

Advisory tools under `lintro review`:

| Flag                       | Meaning                                                              |
| -------------------------- | -------------------------------------------------------------------- |
| _(default)_                | Advisory tools run alongside the diff review, over the changed files |
| `--advisory-tools <names>` | Comma-separated advisory tools, `all` (default), or `none`           |
| `--advisory-only`          | Skip the diff review; scan `--path` values (default `.`)             |
| `--fail-on-findings`       | Exit 1 when advisory tools report findings (default: exit 0)         |
| `--tool-options`           | `tool:option=value` overrides, as in `chk`                           |

Advisory findings never affect the exit code unless `--fail-on-findings` is passed, and
they never contribute to the `chk` health score. With `--output json`, they appear under
an additive `advisory` key so existing consumers of the review JSON keep working.

`idiom-review` offers two modes:

- **per-file** (Mode 1) — flags _idiomatic misses_: code that is correct but verbose,
  e.g. `found = False; for x in items: ...` instead of `any(cond for x in items)`.
- **duplication** (Mode 2) — flags the same utility logic reimplemented across files,
  invisible to any per-file linter, with a suggested extraction point.

The tool ships **disabled by default** and is a no-op until you opt in. Findings are
cached by a content hash under `.lintro-cache/idiom`, so unchanged files cost nothing on
repeat runs. When no AI provider is available (missing SDK, key, or credits), the tool
degrades gracefully to a skipped result rather than failing the run.

```yaml
# .lintro-config.yaml
ai:
  enabled: true
  provider: anthropic
  transport: api
tools:
  idiom-review:
    options:
      enabled: true # opt-in gate (default: false)
      mode: per-file # per-file | duplication | both
      min_confidence: medium # drop findings below this confidence
      max_files: 25 # cap files reviewed per run (cost bound)
```

Or enable it ad hoc from the CLI:

```bash
lintro review --advisory-only --tool-options idiom-review:enabled=true
```

### Custom Review Agents (`.lintro/review-agents/*.md`)

`lintro review` ships a built-in checklist corpus, but house rules ("no raw SQL outside
the repository layer", "every Effect service follows X") are prose, not YAML. Write them
as markdown files under `.lintro/review-agents/` — YAML front matter carries the
machine-readable scope and policy, and the body carries the review instruction.

```markdown
---
name: no-raw-sql
description: SQL must go through the repository layer
include:
  - 'src/**/*.py'
exclude:
  - 'src/repositories/**'
severity: high
strictness: focused
model: default
enabled: true
---

Review the changed code for raw SQL strings executed outside the repository layer. Flag
any direct `cursor.execute` / `connection.execute` call with a string literal, and point
at the repository method that should be used instead.
```

**Front-matter fields:**

| Field         | Type      | Default    | Description                                                      |
| ------------- | --------- | ---------- | ---------------------------------------------------------------- |
| `name`        | string    | _required_ | Unique agent id; becomes the `source` attribution on findings    |
| `include`     | list[str] | _required_ | Globs selecting the changed files the agent reviews              |
| `description` | string    | `""`       | One-line summary shown by `--list-agents`                        |
| `exclude`     | list[str] | `[]`       | Globs removing files from the `include` set                      |
| `severity`    | string    | `P2`       | `P1`/`P2`/`P3` or `high`/`medium`/`low`; applied to all findings |
| `strictness`  | string    | `balanced` | `focused` · `balanced` · `thorough`                              |
| `model`       | string    | `default`  | Optional per-agent model override                                |
| `enabled`     | bool      | `true`     | Set `false` to keep the file but stop running it                 |

**Behavior:**

- Agents are **enabled by default**. Control them with `review.custom_agents` in
  `.lintro-config.yaml`: `true` (run alongside the built-in checklist), `false` (skip
  discovery entirely), or `only` (run agents _instead of_ the built-in checklist).
- An agent runs only when at least one changed file matches its `include` globs after
  `exclude` is applied. Agents that match nothing — and agents with `enabled: false` —
  are reported as skipped and cost nothing.
- Findings merge into the normal terminal, JSON, and PR-comment output, attributed with
  `source: <agent-name>`. The agent's declared `severity` is the severity its findings
  carry, so a `severity: high` agent produces P1 findings that fail the exit gate.
- Each scoped agent is one extra provider call and counts against `ai.max_cost_usd`
  exactly like a built-in checklist chunk.
- A file with invalid front matter is reported with the offending field and skipped —
  never fatal, and the rest of the review still runs.

**Safety:** agent bodies are maintainer-authored workspace content, so they are treated
as untrusted data. A body never becomes the system prompt; it is redacted for secrets,
sanitized, and embedded in the user prompt inside a per-call unique boundary marker with
explicit instructions that nothing inside it can change the model's role or output
contract.

List what would run without spending anything:

```bash
lintro review --list-agents
```

```yaml
# .lintro-config.yaml
review:
  custom_agents: true # true | false | only
```

### Addressed lifecycle on inline threads

When `lintro review --post` runs again on a PR, every finding the new round no longer
reproduces is stamped on its own inline comment rather than only in the sticky summary:

- **Addressed** — the comment gets a `✔ Addressed in <sha> · round N` banner, its
  copy-paste agent prompt is retitled `(historical)`, and the thread is resolved when
  `review.auto_resolve` is true — which it is by default; set it to `false` to opt out
  and resolve the thread by hand.
- **Partially addressed** — a finding reported at several locations resolves only when
  the whole pattern is gone. Progress shows as `✔ 14/20 addressed in <sha> · round N`
  and the thread stays open.
- **Regressed** — a finding that comes back is re-raised on a _fresh_ thread carrying
  `regression · first raised round X, fixed round Y` plus a link to the original. The
  old thread is stamped `↩ Regressed in <sha>` and is never reopened.

Thread resolution is the only configurable half; the banner is always written.

```yaml
# .lintro-config.yaml
review:
  auto_resolve: true # default; set false to resolve threads by hand
```

### Suggested-patch validation

A GitHub `suggestion` block is a one-click commit, so every one is checked against the
real file at HEAD before any surface renders it — the terminal panels, the JSON payload,
and `--post` alike. The file is read at the head revision (locally, or through `gh` in
`--pr` mode); the PR tree is never checked out or executed.

- **Exact match** — the suggestion is posted as-is.
- **Drifted but unique** — the change's `before` block occurs exactly once elsewhere in
  the file, so the hunk and the comment anchor are re-anchored to it.
- **Anything else** — the one-click patch is withheld. The finding keeps its prose and
  its described `fix`, and is tagged with why: `stale_anchor` (the block is not there),
  `ambiguous_anchor` (it is there more than once), or `file_missing` (the file is
  unreadable at HEAD).

Drops are never silent. Terminal output marks each affected finding and prints a run
total, `--output json` carries `suggestions_dropped` plus a
`suggestions_dropped_by_reason` tally alongside a per-finding `suggestion_dropped` tag,
and the sticky comment states the count and reasons.

### Review coverage completeness

A capped CLI review is **not a guaranteed full finding set**. Under `--transport cli`,
every chunk prompt carries the `ai.cli_max_findings_per_call` ceiling, and a chunk that
still exhausts the provider's output-token cap is retried once at a tighter ceiling. In
both cases every chunk is still reviewed — but the model was told to stop at N findings,
so lower-severity issues beyond the cap may exist and go unreported.

That is recorded and surfaced rather than left silent:

- `ReviewMetadata.coverage_degradations` holds one `CoverageDegradation` per limit
  event, each with a `reason` (`findings_cap_applied` or `output_exhaustion_retried`),
  the `chunk_index`, and the `findings_cap` that was in force. A chunk that ran under
  the cap and then retried after output exhaustion contributes two entries with the same
  `chunk_index`. `findings_coverage_complete` is the derived "nothing was capped"
  boolean.
- The terminal prints a `⚠ Coverage limited` banner under the run header.
- The GitHub review body (in **📊 Run stats**) and the sticky comment both carry the
  same warning row, and the sticky's run history marks the round `⚠️ coverage limited`.
- `--output json` exposes `findings_coverage_complete`, `coverage_degradations`,
  `findings_cap_applied`, and `output_exhaustion_retried` at the payload root (and
  `coverage_degradations` inside `metadata`). The MCP `lintro_review` payload carries
  all four on its `run` block and hoists only `findings_coverage_complete` to the
  tool-result root, next to `coverage`.

**Coverage limitation is a separate axis from `partial`.** `partial` / `stopped_reason`
mean the run _stopped early_ and planned review work was left undone (built-in chunks or
custom-agent passes, on a cost cap or an interrupt). A findings-cap run finished its
planned work, just not at full depth, so it is reported as its own signal with equal
prominence instead of being folded into `partial`. A run can be both. It is also
distinct from `coverage.complete` in the JSON payload, which says whether every eligible
file was covered at HEAD.

An uncapped, complete run renders exactly as it always has — no banner, no warning row,
`findings_coverage_complete: true`.

This is distinct from the hard `cli_max_diff_bytes` ceiling: a diff over that limit is
refused outright with `DIFF_TOO_LARGE` and is a hard failure, not a degraded success.

### Review readiness verdict

The merge-readiness verdict is derived in code from open-finding severities (never asked
of the model): any P1 → blocked; else any P2 → changes requested; else any P3 → nits
only; else ready. The review prompt calibrates the P2 vs P3 boundary that would
otherwise flip that verdict run-to-run: borderline findings must be P3, and every
finding `description` must name the rubric boundary it used.

A P2 "changes requested" review still exits 0. An open P1 fails the process (`exit 1`).
`--fail-on-findings` is an additional exit-1 gate when advisory tools report findings.
Exit 2 means no review was produced at all (credential, quota, or lintro-side failure).

### Review phase timings

Every `lintro review` run is instrumented with per-phase wall-clock spans (monotonic
clock, always on, no extra provider calls). They answer which phase dominates a slow
review — provider latency, chunking, context collection, or parse/merge — so perf work
is driven by measurement rather than guesswork.

The terminal output carries a one-line summary under the header, ordered by descending
duration so the dominant phase reads first:

```text
total 4m52s — provider 4m10s (7 chunks, max parallel 5, questions 30.2s), context 22.0s, merge 8.0s, resume 1.2s, chunking 0.4s, validation 0.1s
```

The same line is posted to GitHub as a `Timings:` note under the review body's run-stats
block and the sticky comment's `This run` table (and in the run-mechanics footer of
error stickies). `--output-format json` carries the full breakdown in a top-level
`timings` block:

```json
{
  "timings": {
    "total_seconds": 292.0,
    "max_parallel": 5,
    "phases": [
      { "name": "context_collection", "seconds": 22.0, "occurrences": 1 },
      { "name": "chunking", "seconds": 0.4, "occurrences": 1 },
      { "name": "resume_planning", "seconds": 1.2, "occurrences": 1 },
      { "name": "generated_questions", "seconds": 30.2, "occurrences": 7 },
      { "name": "provider", "seconds": 250.0, "occurrences": 1 },
      { "name": "parse_merge", "seconds": 8.0, "occurrences": 1 },
      { "name": "validation", "seconds": 0.1, "occurrences": 1 }
    ],
    "chunks": [
      {
        "chunk_index": 0,
        "files": 3,
        "queued_seconds": 0.0,
        "in_flight_seconds": 61.2,
        "total_seconds": 61.2,
        "failed": false
      }
    ]
  }
}
```

Reading the block:

- `phases` is in first-occurrence order, so it reads chronologically. `provider` is an
  envelope covering the whole chunk fan-out plus any custom-agent passes, matching the
  `phase_timings.provider` key; phases that run once per chunk inside it
  (`generated_questions` at depth ≥ 2, `adversarial` at depth ≥ 3) fold every occurrence
  into one span, and `occurrences` says how many. Those nested spans are already counted
  inside `provider`, and chunks run concurrently, so phase sums can exceed
  `total_seconds` — the sum answers "how much provider work happened", `total_seconds`
  answers "how long did the user wait". The summary line lists nested phases inside the
  provider parenthetical for the same reason, e.g.
  `provider 4m10s (7 chunks, max parallel 5, questions 30.2s)`.
- `validation` is the post-merge tail of the run: provider session teardown and progress
  callbacks, then the pass that decides what survives (context-finding rejection,
  coverage and resume bookkeeping, flag reconciliation). A slow session close therefore
  shows up here rather than only in the total.
- `metadata.duration_seconds` now equals `total_seconds`: it includes the caller's
  context collection and the validation pass, where it previously started just before
  the provider calls. Consumers comparing durations across versions should expect the
  larger figure for the same provider work.
- Each chunk splits its wall clock into `queued_seconds` (waiting on the concurrency
  semaphore) and `in_flight_seconds` (reviewing). A run where queued time dominates is
  capped by the effective concurrency ceiling, not by provider latency. That ceiling is
  `min(chunk count, ai.max_parallel_calls)`, or 1 when a cost cap serializes chunk calls
  (#2154); it is reported as `max_parallel`.
- GitHub posting happens after the result is rendered, so it is outside the measured
  window and has no phase. `metadata.phase_timings` keeps its flat three-key mapping for
  existing consumers.

## Configuration

### Basic Setup

Add the `ai` section to `.lintro-config.yaml`:

```yaml
ai:
  enabled: true # master switch (AND-ed with the toggles below)
  lint: true # AI lint summaries during chk/fmt
  review: true # the `lintro review` AI diff review
  provider: anthropic # or "openai" / "cursor" ("cursor" needs transport: cli)
  transport: api # "api" (SDK) or "cli" (local agent binary); no default
  # model: claude-sonnet-4-6  # uses provider default if omitted
  # api_key_env: ANTHROPIC_API_KEY   # uses provider default if omitted
```

### Feature Toggles

AI features are gated by a master switch plus two per-feature toggles, all off by
default:

- `ai.enabled` — master switch for all AI features.
- `ai.lint` — AI lint summarization injected after `chk`/`fmt` runs.
- `ai.review` — the `lintro review` AI diff-review command.

A feature is active only when the master switch **and** its own toggle are true
(`enabled AND lint`, `enabled AND review`). This lets you, for example, enable AI diff
review without adding AI summaries to every lint run:

```yaml
ai:
  enabled: true
  lint: false
  review: true
```

**Backward compatibility:** a legacy config that sets `ai.enabled: true` without either
sub-toggle keeps the old behaviour — both `lint` and `review` are switched on — and
emits a deprecation warning. Set `ai.lint` and/or `ai.review` explicitly to silence it.

### Full Configuration Reference

Every key below maps 1:1 to a field on `AIConfig` in `lintro/ai/config.py`, which is the
source of truth. All fields are optional; each is shown with its type, default, and
accepted range. Unknown keys under `ai:` are dropped with a warning rather than
rejected, so a stale key never breaks a run — but a typo never takes effect either.

The grouping below (provider, budget, safety, output, cache, advanced) is for
readability only; the loader expects the flat key layout shown.

```yaml
ai:
  # ── Provider & transport ──────────────────────────────────────
  # Master switch — all AI features are disabled when false. AND-ed with the
  # per-feature toggles below. (bool, default: false)
  enabled: true

  # Per-feature toggles (both default to false). Effective only when
  # enabled is also true.
  lint: true # AI lint summaries during chk/fmt
  review: true # the `lintro review` AI diff review

  # Provider: "anthropic", "openai" or "cursor" ("cursor" is CLI-only).
  # (default: anthropic)
  provider: anthropic

  # How to invoke the provider: "api" (SDK) or "cli" (local agent binary).
  # No default — set it explicitly whenever ai.lint or ai.review is enabled.
  # "cursor" requires "cli". See "Transports".
  transport: api

  # Model override (uses provider default if omitted). (str, default: none)
  # model: claude-sonnet-4-6

  # Custom env var for API key (uses provider default if omitted).
  # (str, default: none)
  # api_key_env: MY_CUSTOM_KEY

  # Custom API base URL — enables Ollama, vLLM, Azure OpenAI, or any other
  # OpenAI-compatible endpoint. (str, default: none)
  # api_base_url: http://localhost:11434/v1

  # Provider region hint for data residency; used together with api_base_url
  # for region-specific endpoints. (str, default: none)
  # api_region: eu

  # Ordered fallback model chain — each entry is tried in turn if the primary
  # model fails. (list[str], default: [])
  fallback_models: []

  # Max tokens per API request. (int 1–128000, default: 4096)
  max_tokens: 4096

  # Max retries for transient API errors. (int 0–10, default: 2)
  max_retries: 2

  # API request timeout in seconds. (float >= 1.0, default: 60.0)
  api_timeout: 60.0

  # Retry backoff parameters.
  retry_base_delay: 1.0 # initial delay, seconds (float >= 0.1)
  retry_max_delay: 30.0 # max delay, seconds (float >= 1.0, must be >= base)
  retry_backoff_factor: 2.0 # multiplier per retry (float >= 1.0)

  # ── Budget & cost caps ────────────────────────────────────────
  # Max issues to attempt fixing per run. Counts API calls made, not
  # suggestions returned. (int >= 1, default: 20)
  max_fix_attempts: 20

  # Concurrent AI provider calls (fixes and review chunk fan-out).
  # Honored even when max_cost_usd is set. (int 1–20, default: 5)
  max_parallel_calls: 5

  # Spend ceiling per AI session, in USD; the run stops
  # scheduling new calls once spent+reserved reaches the cap. null disables
  # it. A cost cap serializes chunk reviews (one provider call at a time,
  # #2154) so the resume queue cannot invert; the call already in flight
  # when the ceiling is hit still finishes, so the final total may
  # overshoot by up to one call's cost.
  # (float >= 0 | null, default: null)
  max_cost_usd: null

  # Token budget for a fix prompt before context is trimmed — a soft budget,
  # see "Data & Privacy". (int >= 1000, default: 12000)
  max_prompt_tokens: 12000

  # ── CLI-transport review limits (#1967) ───────────────────────
  # Per-chunk diff token budget under --transport cli; forces the semantic
  # chunker to split diffs a single CLI turn cannot finish.
  # (int >= 1000, default: 24000)
  cli_max_diff_tokens: 24000

  # Hard ceiling on the full unified-diff byte size under --transport cli;
  # larger diffs fail fast with a --paths / --transport api advisory.
  # (int >= 10000, default: 1500000)
  cli_max_diff_bytes: 1500000

  # Max findings one CLI review call may emit. The cap is a prompt contract
  # (the model is instructed to stop at the cap and summarize overflow), not
  # a post-parse truncation; a chunk that still exhausts the 32k output cap
  # retries once with a tighter cap, and truncated responses fall back to
  # the schema-retry / unstructured-recovery ladder.
  # (int 1–50, default: 12)
  cli_max_findings_per_call: 12

  # Re-prompt to refine a fix that failed verification. (int 0–3, default: 1)
  max_refinement_attempts: 1

  # ── Safety & filtering ────────────────────────────────────────
  # How to handle prompt-injection patterns detected in source files or
  # diagnostics: "warn" logs and continues, "block" skips the affected file,
  # "off" disables detection. (one of: off | warn | block, default: warn)
  sanitize_mode: warn

  # Minimum confidence for AI fix suggestions; anything below the threshold is
  # discarded. (one of: low | medium | high, default: low)
  min_confidence: low

  # Restrict AI processing to matching paths / rules (glob patterns).
  # Empty means "no filter". (list[str], default: [])
  include_paths: []
  exclude_paths: []
  include_rules: []
  exclude_rules: []

  # ── Output & apply behaviour ──────────────────────────────────
  # Set true to always run --fix in chk without the CLI flag.
  # (bool, default: false)
  default_fix: false

  # Auto-apply fixes without interactive review (use with caution).
  # (bool, default: false)
  auto_apply: false

  # Auto-apply deterministic style fixes (e.g. E501) in non-interactive/json
  # runs. (bool, default: true)
  auto_apply_safe_fixes: true

  # Preview mode: show AI fix suggestions without applying them.
  # (bool, default: false)
  dry_run: false

  # Interactive mode: validate immediately after each accepted group.
  # (bool, default: false)
  validate_after_group: false

  # Show token count and cost estimate in output. (bool, default: true)
  show_cost_estimate: true

  # Extra diagnostic logging for AI operations. (bool, default: false)
  verbose: false

  # Stream AI responses token-by-token in interactive mode.
  # (bool, default: false)
  stream: false

  # Post AI summaries and inline fix suggestions as PR review comments when
  # running in GitHub Actions. (bool, default: false)
  github_pr_comments: false

  # CI exit-code control: when true, an AI error (fail_on_ai_error) or an
  # unfixed/failed AI fix (fail_on_unfixed) contributes to a non-zero exit
  # code. (bool, default: false)
  fail_on_ai_error: false
  fail_on_unfixed: false

  # Lines of surrounding context sent to AI for fix generation.
  # (int 1–100, default: 15)
  context_lines: 15

  # Max lines above/below target for line-targeted fix search.
  # (int 1–50, default: 5)
  fix_search_radius: 5

  # ── Git checkpoints ───────────────────────────────────────────
  # Refs kept under refs/lintro/checkpoints/, this run included.
  # 0 keeps only the current run. (int >= 0, default: 10)
  checkpoint_retention: 10

  # Also checkpoint before `lintro format` mutates files.
  # (bool, default: false)
  checkpoint_fmt: false

  # ── Suggestion cache ──────────────────────────────────────────
  # Deduplicate identical fix requests across runs. (bool, default: false)
  enable_cache: false

  # Cache entry time-to-live, seconds. (int >= 60, default: 3600)
  cache_ttl: 3600

  # Max cached entries before eviction. (int >= 1, default: 1000)
  cache_max_entries: 1000

  # ── Anthropic CLI transport ───────────────────────────────────
  # Whether to pass "--bare" to the "claude" binary. "--bare" drops the CLI's
  # agentic tool surface but also disables OAuth session login, so it only
  # authenticates against an API key. "auto" sends it only when a key is
  # reachable (ANTHROPIC_API_KEY or an apiKeyHelper), so a subscription login
  # keeps working. Override per run with LINTRO_CLI_BARE.
  # (auto | always | never, default: auto)
  cli_bare: auto

  # ── Cursor workspace trust ──
  # Choosing provider: cursor grants workspace trust (passes "--trust" to the
  # agent CLI). Set false to restore the agent's interactive trust prompt.
  # (bool, default: true)
  cursor_trust_workspace: true

  # ── Advanced / trust (leave off unless you understand the risk) ──
  # Let the git-native (CLI transport) review path delegate diff retrieval to
  # the provider instead of embedding a redacted diff. Security risk: a
  # delegated diff bypasses lintro's secret-redaction choke point — see the
  # warning under "Data & Privacy". (bool, default: false)
  review_allow_unredacted_git_native: false
```

### Config Defaults for CLI Flags

If you always want `--fix` without typing it, set the default in config:

```yaml
ai:
  enabled: true
  transport: api
  default_fix: true # equivalent to always passing --fix
```

CLI flags always override config: passing `--fix` on the CLI turns it on even if
`default_fix: false`, and omitting it falls back to the config value.

### Providers

#### [Anthropic](https://docs.anthropic.com/) (default)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

```yaml
ai:
  provider: anthropic
  # model: claude-sonnet-4-6  # default
```

See the [Anthropic API docs](https://docs.anthropic.com/en/api/) for model options and
pricing.

#### [OpenAI](https://platform.openai.com/docs/)

```bash
export OPENAI_API_KEY=sk-...
```

```yaml
ai:
  provider: openai
  # model: gpt-4o  # default
```

See the [OpenAI API docs](https://platform.openai.com/docs/api-reference/) for model
options and pricing.

## Transports

Lintro reaches a provider one of two ways, selected by `ai.transport`:

- **`api`** — the provider's Python SDK over HTTPS. Requires the `ai` extra.
- **`cli`** — a subprocess call to a locally installed agent binary (`claude`, `codex`,
  Cursor's `agent`).

Timeouts, cost caps, failure vocabulary, and the meaning of reported `$` figures are
**transport-scoped** — see [AI review transports](ai-review-transports.md) for the
decision table and `ai.transports.*` profiles (#1923).

`ai.transport` has **no default**, so set it explicitly whenever `ai.lint` or
`ai.review` is enabled. Omitting it is not fatal: `lintro doctor` reports the config as
incompatible, and the provider factory falls back to `api` so an existing run keeps
working. That fallback exists for backward compatibility — legacy configs that set only
`ai.enabled: true` (which implicitly switches `lint` and `review` on) rely on it — and
is not something to depend on in new config.

`cursor` is a CLI-only provider: pair it with `transport: cli`. `anthropic` and `openai`
support both transports.

```yaml
ai:
  enabled: true
  review: true
  provider: anthropic
  transport: api # or "cli"
```

Both `lintro check` and `lintro review` accept `--transport api|cli` to override the
config for a single invocation. `lintro review` also accepts `--provider`, `--model`,
`--review/--no-review`, and `--max-cost-usd`. Environment variables
(`LINTRO_AI_PROVIDER`, `LINTRO_AI_MODEL`, `LINTRO_AI_TRANSPORT`, `LINTRO_AI_ENABLED`,
`LINTRO_AI_REVIEW`, `LINTRO_AI_MAX_COST_USD`) apply to every AI surface and lose to CLI
flags. There is no `--enabled` flag.

### Invocation overrides

Resolution order for `provider`, `model`, `transport`, `enabled`, `review`, and
`max_cost_usd` is:

```text
CLI flag > environment variable > .lintro-config.yaml > built-in default
```

Overlays replace the active transport profile's cost cap
(`ai.transports.api.max_cost_usd` / `ai.transports.cli.max_cost_usd_advisory`) as well
as the legacy `ai.max_cost_usd` scalar.

| Variable / flag                                           | Overrides         | Notes                                                                                        |
| --------------------------------------------------------- | ----------------- | -------------------------------------------------------------------------------------------- |
| `LINTRO_AI_PROVIDER` / `lintro review --provider`         | `ai.provider`     | `anthropic`, `openai`, or `cursor`                                                           |
| `LINTRO_AI_MODEL` / `lintro review --model`               | `ai.model`        | any model id; empty env falls through                                                        |
| `LINTRO_AI_TRANSPORT` / `--transport`                     | `ai.transport`    | `api` or `cli`                                                                               |
| `LINTRO_AI_ENABLED`                                       | `ai.enabled`      | `1`/`0`/`true`/`false`. `=1` does not turn on `ai.review` or `ai.lint`. No `--enabled` flag. |
| `LINTRO_AI_REVIEW` / `lintro review --review/--no-review` | `ai.review`       | `1`/`0`/`true`/`false`. The master `ai.enabled` switch must also be on.                      |
| `LINTRO_AI_MAX_COST_USD` / `lintro review --max-cost-usd` | `ai.max_cost_usd` | USD cap. Overlay `uncapped` lifts. Overlay `0` is rejected (YAML `0` is $0).                 |

Unset variables are absent (fall through). Invalid values fail at resolution with a
message naming the variable and the accepted values — they never silently use the config
default. Review output annotates each resolved field with its source
(`provider: cursor (env)`, `max cost: uncapped (env)`).

```bash
# Try Cursor locally without dirtying .lintro-config.yaml
LINTRO_AI_PROVIDER=cursor LINTRO_AI_TRANSPORT=cli lintro review --uncommitted

# Same thing with flags (flags win if both are set)
lintro review --uncommitted --provider cursor --model cursor-grok-4.6-high --transport cli

# Run without a cost cap (`uncapped`; overlay `0` is an error)
LINTRO_AI_MAX_COST_USD=uncapped lintro review --uncommitted
lintro review --uncommitted --max-cost-usd uncapped

# Kill switch for this environment
LINTRO_AI_ENABLED=0 lintro check .

# Enable diff review without changing the committed config
LINTRO_AI_ENABLED=1 LINTRO_AI_REVIEW=1 lintro review --uncommitted
```

### Transport authentication

**Every transport needs a credential of its own — CLI transport is not
credential-free.**

| Provider    | Transport | Credential                                                        |
| ----------- | --------- | ----------------------------------------------------------------- |
| `anthropic` | `api`     | `ANTHROPIC_API_KEY`                                               |
| `anthropic` | `cli`     | `claude` login session, `ANTHROPIC_API_KEY`, or an `apiKeyHelper` |
| `openai`    | `api`     | `OPENAI_API_KEY`                                                  |
| `openai`    | `cli`     | `codex login` session (`~/.codex/auth.json`) or `CODEX_API_KEY`   |
| `cursor`    | `cli`     | `agent login` session or `CURSOR_API_KEY` (CLI-only provider)     |

`ai.api_key_env` overrides the API-transport variable name if you keep the key somewhere
else.

> **Anthropic `--transport cli` and the `--bare` flag.**
>
> `claude --bare` runs the CLI without its agentic tool surface, but it also disables
> OAuth session login — in bare mode the binary authenticates only against an API key.
> Lintro therefore chooses the flag per invocation (`ai.cli_bare`, default `auto`):
>
> - An API key is reachable (`ANTHROPIC_API_KEY` is set, or a Claude Code settings file
>   declares an `apiKeyHelper`) → lintro sends `--bare`, and the call bills that key
>   exactly like `--transport api`.
> - No API key is reachable → lintro omits `--bare`, and the call uses your `claude`
>   login session, billed to that subscription.
>
> Force either mode explicitly with `ai.cli_bare: always|never` in config, or with the
> `LINTRO_CLI_BARE=always|never` environment variable (the environment wins). Codex and
> Cursor are unaffected — both accept a CLI login session. See
> [#1838](https://github.com/lgtm-hq/py-lintro/issues/1838).

### Failures are visible, never a green no-op

A missing, rejected, or depleted credential is reported, not swallowed. Lintro walks a
`presence → liveness → invoke` chain, and each step's failure short-circuits to a
**visible** skip or failure:

- **Presence** — is the SDK importable, the binary on `PATH`, the key variable set?
- **Liveness** — under `api`, a minimal one-token real call, because a valid key with a
  depleted balance authenticates and lists models but cannot serve a review. Under
  `cli`, presence plus the free `--version` / `--help` capability gate (no quota spent,
  so the result is reported as unverified quota).
- **Invoke** — an auth or quota error at call time is classified through the same
  taxonomy: `auth_failed`, `no_quota`, `rate_limited`, `unreachable`,
  `incompatible_cli`, `missing_credential`.

Probe it directly:

```bash
lintro doctor              # presence checks (free)
lintro doctor --ai-liveness # adds the liveness probe (one minimal API call on `api`)
```

`lintro review` distinguishes the two red states by exit code, so a wrapper can never
mistake one for the other:

| Exit | Meaning                                                                                                                |
| ---- | ---------------------------------------------------------------------------------------------------------------------- |
| `0`  | A review was produced (clean, or findings below P1)                                                                    |
| `1`  | A review was produced and contains P1 findings                                                                         |
| `2`  | **No review was produced** — missing/dead credential, depleted balance, unreachable provider, or a lintro-side failure |

### CLI compatibility floors

Each CLI transport declares a **minimum supported version** of the agent binary. These
are _known-incompatible-below_ floors, not known-good pins: a binary below the floor
predates the flag surface lintro drives, so lintro fails with an actionable upgrade hint
instead of a confusing runtime error.

| Provider    | Binary   | Minimum version | Upgrade                                           |
| ----------- | -------- | --------------- | ------------------------------------------------- |
| `anthropic` | `claude` | `2.0.0`         | `npm install -g @anthropic-ai/claude-code@latest` |
| `openai`    | `codex`  | `0.20.0`        | `npm install -g @openai/codex@latest`             |
| `cursor`    | `agent`  | `2025.1.1`      | `curl https://cursor.com/install -fsS \| bash`    |

Source of truth: `lintro/ai/providers/cli_contracts.py`.

Above the floor, lintro tolerates flag-surface drift with a three-part guard:

1. **Version floor** — refuse binaries known to be too old.
2. **Proactive gate** — optional flags (`--json-schema-name`, `--resume`,
   `--output-schema`, `--trust`) are checked against the binary's `--help` before being
   sent, and simply dropped when unsupported. Only their extra capability is lost.
3. **Reactive backstop** — a call that still fails with `unknown option` drops the
   offending optional flag and retries.

Required flags are not gated — dropping them would degrade a review silently. Instead,
CI's contract tests assert the installed binaries still advertise them, so drift breaks
CI rather than a user's review.

### Transports in CI

Both transports need their credential injected explicitly; nothing is inherited from a
developer's login.

- **Fork PRs cannot read secrets.** GitHub withholds repository secrets from
  `pull_request` runs originating in a fork, so any AI job must either skip on forks or
  degrade to a visible skip. Lintro's own `ai-review.yml` requires
  `head.repo.full_name == github.repository`, so the keyed job never runs for a fork.
- **Never default an unset secret.** Forwarding `${{ secrets.X }}` unset lets the run
  report a visible skip naming the missing credential; substituting a placeholder turns
  it into a false pass.
- **Trusted install.** Lintro installs itself from the PR's _base_ ref before the step
  that holds the provider credential, so PR-controlled code never executes with the
  secret in scope. The PR is reviewed as data (the diff is fetched through the GitHub
  API).
- **A subscription works in CI, on the `cli` transport.** Lintro's own `ai-review.yml`
  runs `ai.transport: cli` against a version-pinned `claude` CLI
  (`npm install -g @anthropic-ai/claude-code@<pin>`) authenticated by a
  `CLAUDE_CODE_OAUTH_TOKEN` secret — no API key involved. Two things make that work:
  keep `ANTHROPIC_API_KEY` **out** of the step env, and set `LINTRO_CLI_BARE: never`,
  because `--bare` disables OAuth session login (see the `--bare` note above). Set
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and `DISABLE_AUTOUPDATER=1` to keep the
  binary's egress and version predictable under an egress allowlist.
- **`ai.max_cost_usd` is API-path accounting.** Lintro prices the tokens it billed
  itself, so under the `cli` transport the cap is advisory — the call bills the
  subscription (or, in bare mode with a reachable API key, that key — see the billing
  note above). Setting a cap serializes chunk reviews to one provider call at a time
  (#2154), so the session can overshoot by at most the call in flight when the ceiling
  is hit. Review metadata records per-phase timings (see "Review phase timings" above)
  so wall-clock regressions are visible in JSON / MCP output.
- **Two tiers of contract testing.** The flag-surface tier runs `--version` / `--help`
  only — no credential, no quota — on every PR. The real-invocation tier spends quota
  and runs weekly, gated behind the free tier.

Security notes: provider API keys are secrets — store them in the repository/org secret
store (or a local shell profile), never in `.lintro-config.yaml`, which only names the
_variable_ through `ai.api_key_env`. A key with billing attached should be scoped and
rotatable; `lintro doctor --ai-liveness` is the cheap way to confirm a rotation took
effect.

## Environment Support

AI output adapts to the environment:

| Environment    | Rendering                                |
| -------------- | ---------------------------------------- |
| Terminal       | Rich Panels with color and structure     |
| GitHub Actions | `::group::` / `::endgroup::` collapsible |
| Markdown       | `<details>` / `</details>` collapsible   |
| JSON           | `ai_summary` and `metadata` fields       |

### JSON Output

When using `--output-format json`, AI data is included in the output.

> **Note:** the per-tool key is `metadata`. It is deliberately not named `ai_metadata`
> because it is not AI-specific: osv-scanner writes its suppression classifications
> there with AI fully disabled. The old `ai_metadata` key was removed after its
> deprecation cycle — consumers must read `metadata`.

```json
{
  "results": [
    {
      "tool": "ruff",
      "issues": [...],
      "metadata": {
        "summary": {
          "overview": "Code quality assessment...",
          "key_patterns": ["Pattern 1", "Pattern 2"],
          "priority_actions": ["Action 1", "Action 2"],
          "estimated_effort": "20-30 minutes"
        },
        "fix_suggestions": [...]
      }
    }
  ],
  "summary": {...},
  "ai_summary": {
    "overview": "Code quality assessment...",
    "key_patterns": ["Pattern 1", "Pattern 2"],
    "priority_actions": ["Action 1", "Action 2"],
    "estimated_effort": "20-30 minutes"
  }
}
```

### GitHub Actions

In CI, AI summary appears as a collapsible group in the workflow log. No special
configuration needed — Lintro auto-detects `GITHUB_ACTIONS=true`.

## Cost Control

### Estimated Costs

AI features use minimal API calls:

| Feature          | API Calls                 | Typical Cost |
| ---------------- | ------------------------- | ------------ |
| AI Summary       | 1 per run                 | ~$0.01       |
| Fix suggestions  | 1 per issue (up to limit) | ~$0.01 each  |
| Post-fix summary | 1 after fix review        | ~$0.01       |

### Reducing Costs

1. **Limits** — `max_fix_attempts` (default 20) caps API calls
2. **Opt-in flags** — `--fix` is opt-in; only the summary runs by default (1 call)
3. **Cost display** — `show_cost_estimate: true` shows token usage and estimated cost
   after each AI operation

### Disabling AI

```yaml
ai:
  enabled: false # disables all AI features
```

Or simply don't install the extra:

```bash
uv pip install lintro  # no AI support
```

## Retry and Error Handling

AI API calls use exponential backoff retry:

- **Max retries:** 2 (3 total attempts)
- **Backoff:** 1s, 2s (capped at 30s)
- **Retried errors:** rate limits, transient provider errors
- **Not retried:** authentication errors (fail immediately)

AI failures never break the main linting flow. If the provider is unavailable, you get
your normal linting results with a one-line notice:

```text
AI: enhancement unavailable
```

### Non-JSON review responses

`lintro review` asks the model for a JSON object. When an answer comes back as prose
instead, the findings it contains are recovered rather than discarded:

1. The response is parsed, including JSON embedded in surrounding prose.
2. If that fails, lintro makes **exactly one** retry asking the model to re-emit its
   answer in the required schema. The retry is charged against the same per-call
   `ai.api_timeout` budget as the original call (capped at half of it), and is skipped
   entirely when too little of that budget remains.
3. If the retry also fails, the prose is reported as a single low-severity "unstructured
   review output" finding carrying the complete answer, and the chunk completes instead
   of aborting.

Every response that fails to parse — at the CLI-envelope layer or the review layer — is
written in full to `.lintro-cache/ai/raw-responses/`, so nothing the model produced is
lost to truncation.

## Pre-Execution Summary

When AI is enabled, the pre-execution summary table includes AI configuration:

```text
┌───────────────┬──────────────────────────────────┐
│ Setting       │ Value                            │
├───────────────┼──────────────────────────────────┤
│ AI            │ enabled                          │
│               │   provider: anthropic            │
│               │   model: claude-sonnet-4-...     │
│               │   parallel: 5 workers            │
│               │   safe-auto-apply: on            │
│               │   verify-fixes: off               │
└───────────────┴──────────────────────────────────┘
```

This shows provider status, SDK availability, API key presence, and operational settings
at a glance.

## Docker with AI

Two images are published. Which one you need depends on the transport.

| Image                          | Contains                                                                                          | Use when                                               |
| ------------------------------ | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `ghcr.io/lgtm-hq/py-lintro`    | The lint toolchain. Lean; **no** provider SDKs, **no** agent CLIs.                                | Linting only, or AI off.                               |
| `ghcr.io/lgtm-hq/py-lintro-ai` | Everything above **plus** the `ai` extra and the baked `claude`, `codex` and Cursor `agent` CLIs. | `--transport api` or `--transport cli` in a container. |

Both carry the same tag scheme (`latest`, `0.94`, `0.94.2`, `sha-<commit>`) and are
cosign-signed. The `ai` variant is a strict superset built `FROM` the base image's
`full` stage, so nothing is lost by using it — it is simply larger, which is why the
lint image stays free of it.

To use AI features, pass your API key as an environment variable. The **provider**
defaults to `ai.provider` in the mounted `.lintro-config.yaml`, and can be overridden
per run with `LINTRO_AI_PROVIDER` or `lintro review --provider` without editing that
file. `--transport` (and `LINTRO_AI_TRANSPORT`) override the invocation path. The
examples pass the key by **name** (`-e VAR`, no `=value`), so the secret is inherited
from the shell's environment instead of appearing in the container's argument list.

```bash
# API transport, with `ai: {provider: anthropic}` in the mounted config
docker run --rm \
  -e ANTHROPIC_API_KEY \
  -v $(pwd):/code \
  ghcr.io/lgtm-hq/py-lintro-ai:latest check . --transport api

# API transport, with `ai: {provider: openai}` in the mounted config
docker run --rm \
  -e OPENAI_API_KEY \
  -v $(pwd):/code \
  ghcr.io/lgtm-hq/py-lintro-ai:latest check . --transport api

# CLI transport — the agent binaries are already on PATH in this image.
# The credential is still required (see "Transport authentication" above).
docker run --rm \
  -e ANTHROPIC_API_KEY \
  -v $(pwd):/code \
  ghcr.io/lgtm-hq/py-lintro-ai:latest check . --transport cli
```

The agent CLIs live in `/opt/ai-tools`, installed from the digest-pinned
`ghcr.io/lgtm-hq/lintro-ai-tools` base image and refreshed weekly. Only
`/opt/ai-tools/bin` goes on `PATH`, so the lint toolchain's own runtimes are untouched.
Because the bundled CLIs are rebuilt on a weekly cadence while the vendors release far
more often, the capability guard above is what absorbs the lag.

Building locally, the base image adds the AI _extras_ (SDKs, not CLIs) with
`WITH_AI=true`, and the full `ai` variant is its own build target:

```bash
docker build --build-arg WITH_AI=true -t lintro-ai .   # API transport only
docker build --target ai -t lintro-ai-full .           # + baked agent CLIs
```

Outside Docker, the agent CLIs are bring-your-own: install them yourself (pip, Homebrew,
npm, the vendor installer) and keep them at or above the
[compatibility floors](#cli-compatibility-floors).

## Troubleshooting

### AI: enhancement unavailable

**SDK not installed:**

```bash
uv pip install 'lintro[ai]'
```

**API key missing:**

```bash
# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
export OPENAI_API_KEY=sk-...

# Or use a custom env var
# .lintro-config.yaml
# ai:
#   api_key_env: MY_CUSTOM_API_KEY
```

**Unknown provider:**

Only `anthropic`, `openai` and `cursor` are supported (`cursor` is CLI-transport only).
Check your `ai.provider` value.

### Rate Limits

If you hit rate limits, the retry logic handles transient 429 errors automatically. For
persistent rate limiting:

- Reduce `max_parallel_calls` (e.g., from 5 to 2)
- Reduce `max_fix_attempts`

### High Costs

- Check `show_cost_estimate: true` is set to monitor usage
- Lower `max_fix_attempts` (default 20) if fix generation is too expensive
- Avoid `default_fix: true` in config unless you want fixes every run
- Use `--fix` only when needed

## Data & Privacy

### What is sent to the AI provider

- **Summary mode** (`lintro check`): An issue digest containing error codes, counts,
  issue messages, and workspace-relative file paths. No source code is sent.
- **Fix mode** (`--fix` or `lintro format`): The source of the file carrying the issue,
  plus the issue message and error code. **How much of that file is sent depends on its
  size — often all of it.** See below.
- **Review mode** (`lintro review`): The unified diff under review — changed lines with
  their surrounding hunk context — the workspace-relative paths of the changed files,
  and, when lint results are available, a digest of them. The diff passes through
  lintro's secret-redaction step first.

> **Warning — `ai.review_allow_unredacted_git_native` sends unredacted diffs.**
>
> With this option enabled, the CLI transport asks the provider to run `git diff` itself
> instead of embedding lintro's redacted diff. The result never crosses lintro's
> redaction choke point, so **any secret, token or other sensitive content present in
> the diff reaches the provider's backend verbatim**. It defaults to `false`. Enable it
> only in a controlled, trusted environment, on diffs you have confirmed carry no
> secrets, and only when delegated retrieval is needed for a very large diff.

### How much source code fix mode sends

Fix mode is the one path that can send a whole source file. The amount is
size-dependent, governed by `FULL_FILE_THRESHOLD` (500 lines) in
`lintro/ai/fix_context.py`:

- **Files at or under 500 lines** — the **entire file** is attempted first, so the model
  can reason about the whole file when generating a fix. It is sent in full only if the
  resulting prompt fits within `max_prompt_tokens`; otherwise lintro falls back to the
  window below.
- **Files over 500 lines** — only a window around the issue line is sent
  (`context_lines`, default 15 lines either side). The same windowing catches a small
  file whose full contents would blow the prompt token budget.
- **Batch path** — when several fixable issues share one file, the batch prompt embeds
  the sanitized **full file regardless of line count**, falling back to per-issue
  prompts only when the estimated batch prompt exceeds `max_prompt_tokens`.
- **Token budget** — `max_prompt_tokens` (default 12000) is a _soft_ budget. The
  single-issue path halves the window down to a 3-line floor and then sends the prompt
  anyway if it is still over; refinement prompts build a fixed window with no budget
  check at all. Very wide lines or refinement retries can therefore exceed the cap.

Secret redaction and prompt-injection scanning are applied to every prompt lintro
assembles, regardless of context size. The one documented exception is
`ai.review_allow_unredacted_git_native`, which delegates diff retrieval to the provider
and so bypasses redaction entirely (see the warning above). If sending whole files is
unacceptable in your environment, keep fix mode off — `max_prompt_tokens` alone is not a
guarantee.

### What is NOT sent

- **Absolute paths** — all paths are made relative to the workspace root before sending
- **Other project files** — in summary and fix modes, only files with reported issues
  are read; in review mode, only files that the diff under review touches
- **Detected secrets** — recognized secret patterns are redacted from the file content,
  the issue message, and any context window before the prompt leaves lintro

### Workspace boundary enforcement

AI fix suggestions are validated against the workspace root. Fixes targeting files
outside the workspace are rejected and never applied.

### Local transcript logging (opt-in)

Raw provider request/response traffic can be written as NDJSON under
`.lintro-cache/ai/transcripts/` for debugging. This is **off by default**. Enable with
`ai.transcript_logging: true` or `LINTRO_AI_TRANSCRIPT=1`.

- Transcripts stay on the local machine only (not uploaded by lintro)
- Payloads are secret-redacted before write; API keys and auth headers are never logged
- Older transcript files are pruned (default: keep last 10 runs via
  `ai.transcript_retention`)

### Important notes

- AI suggestions can hallucinate incorrect fixes — always review before accepting
- See your provider's privacy policy for data retention:
  [Anthropic](https://www.anthropic.com/privacy),
  [OpenAI](https://openai.com/policies/privacy-policy)
