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

### AI Fixes in `format`

When running `lintro format`, tools auto-fix what they can. For remaining unfixable
issues, the AI generates fix suggestions and presents them interactively (same UX as
`--fix` in `check`). After the session, a post-fix summary wraps up what was
accomplished.

### AI Idiom Review (`idiom-review` tool)

Unlike the AI summary and `--fix` flows (where AI _explains_ or _fixes_ issues that
linters found), the `idiom-review` tool uses AI to _find_ issues that syntax-matching
linters structurally cannot. It has no external binary — it runs through the existing AI
provider abstraction, respecting the same retry, fallback, and cost-budget controls.

It offers two modes:

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
lintro chk --tools idiom-review --tool-options idiom-review:enabled=true
```

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

  # Concurrent API calls during fix generation. (int 1–20, default: 5)
  max_parallel_calls: 5

  # Hard ceiling on total spend per AI session, in USD; the run stops
  # requesting fixes once the estimate reaches the cap. null disables it.
  # (float >= 0 | null, default: null)
  max_cost_usd: null

  # Token budget for a fix prompt before context is trimmed — a soft budget,
  # see "Data & Privacy". (int >= 1000, default: 12000)
  max_prompt_tokens: 12000

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

  # ── Advanced / trust (leave off unless you understand the risk) ──
  # Pass "--trust" to the Cursor agent CLI. Security risk: the Cursor provider
  # can be fed prompt-injectable content (e.g. fork-PR diffs), so keep this
  # false outside fully trusted local workspaces. (bool, default: false)
  cursor_trust_workspace: false

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
config for a single invocation.

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
  that holds `ANTHROPIC_API_KEY`, so PR-controlled code never executes with the secret
  in scope. The PR is reviewed as data (the diff is fetched through the GitHub API).
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

> **Deprecation:** the per-tool key is now `metadata`. The old `ai_metadata` key is
> still emitted with identical content for one release cycle and will be removed in a
> future release — migrate consumers to `metadata`. The key was renamed because it is
> not AI-specific: osv-scanner writes its suppression classifications there with AI
> fully disabled.

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
      },
      "ai_metadata": { "...": "deprecated duplicate of metadata" }
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

To use AI features, pass your API key as an environment variable. The **provider** comes
from `ai.provider` in the `.lintro-config.yaml` of the mounted workspace — there is no
provider CLI flag or environment override, and exporting `OPENAI_API_KEY` alone does not
switch lintro off its `anthropic` default. `--transport` is the one part of the AI
config the CLI can override per run. The examples pass the key by **name** (`-e VAR`, no
`=value`), so the secret is inherited from the shell's environment instead of appearing
in the container's argument list.

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

### Important notes

- AI suggestions can hallucinate incorrect fixes — always review before accepting
- See your provider's privacy policy for data retention:
  [Anthropic](https://www.anthropic.com/privacy),
  [OpenAI](https://openai.com/policies/privacy-policy)
