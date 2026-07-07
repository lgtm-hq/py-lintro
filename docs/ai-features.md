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

## Configuration

### Basic Setup

Add the `ai` section to `.lintro-config.yaml`:

```yaml
ai:
  enabled: true
  provider: anthropic # or "openai"
  # model: claude-sonnet-4-6  # uses provider default if omitted
  # api_key_env: ANTHROPIC_API_KEY   # uses provider default if omitted
```

### Full Configuration Reference

Every field below maps 1:1 to `AIConfig` in `lintro/ai/config.py`, which is the source
of truth. Unknown keys under `ai:` are ignored with a warning, so a typo never silently
changes behavior. All fields are optional except that `transport` is required whenever
`enabled: true`.

The block below shows every field with its type, default, and accepted range. Fields are
grouped by concern (provider, budget, safety/filtering, output, cache) for readability
only — the flat key layout is what the loader expects.

```yaml
ai:
  # ── Master toggle ─────────────────────────────────────────────
  # All AI features are disabled when false. (bool, default: false)
  enabled: true

  # ── Provider ──────────────────────────────────────────────────
  # Which backend to use: "anthropic", "openai", or "cursor".
  # (default: anthropic)
  provider: anthropic

  # How to invoke the provider. REQUIRED when enabled is true.
  # "api" uses the provider SDK; "cli" shells out to a local binary
  # (e.g. the Cursor agent). (one of: api | cli, default: none)
  transport: api

  # Model override (uses the provider default if omitted).
  # (str, default: none)
  # model: claude-sonnet-4-6

  # Custom env var to read the API key from (uses the provider
  # default env var if omitted). (str, default: none)
  # api_key_env: MY_CUSTOM_KEY

  # Custom API base URL. Enables Ollama, vLLM, Azure OpenAI, or any
  # other OpenAI-compatible endpoint. (str, default: none)
  # api_base_url: http://localhost:11434/v1

  # Provider region hint for data residency; used together with
  # api_base_url for region-specific endpoints. (str, default: none)
  # api_region: eu

  # Ordered fallback model chain. If the primary model fails, each
  # entry is tried in turn. (list[str], default: [])
  fallback_models: []

  # Max tokens per API request. (int 1–128000, default: 4096)
  max_tokens: 4096

  # Max retries for transient API errors. (int 0–10, default: 2)
  max_retries: 2

  # API request timeout in seconds. (float >= 1.0, default: 60.0)
  api_timeout: 60.0

  # Retry backoff parameters.
  retry_base_delay: 1.0 # initial delay in seconds (float >= 0.1)
  retry_max_delay: 30.0 # max delay in seconds (float >= 1.0, must be >= base)
  retry_backoff_factor: 2.0 # multiplier per retry (float >= 1.0)

  # ── Budget & cost caps ────────────────────────────────────────
  # Max issues to attempt fixing per run. Counts API calls made,
  # not suggestions returned. (int >= 1, default: 20)
  max_fix_attempts: 20

  # Concurrent API calls during fix generation. (int 1–20, default: 5)
  max_parallel_calls: 5

  # Hard ceiling on total spend per AI session in USD; the run stops
  # requesting fixes once the estimate reaches this cap. null disables
  # the limit. (float >= 0 | null, default: null)
  max_cost_usd: null

  # Token budget for a single fix prompt before context is trimmed
  # (see "Data & Privacy" below). (int >= 1000, default: 12000)
  max_prompt_tokens: 12000

  # Auto-refine unverified fixes by re-prompting when a fix fails
  # verification. (int 0–3, default: 1)
  max_refinement_attempts: 1

  # ── Safety & filtering ────────────────────────────────────────
  # How to handle prompt-injection patterns detected in source files
  # or diagnostics: "warn" logs and continues, "block" skips the
  # affected file, "off" disables detection.
  # (one of: off | warn | block, default: warn)
  sanitize_mode: warn

  # Minimum confidence level for AI fix suggestions; suggestions
  # below the threshold are discarded.
  # (one of: low | medium | high, default: low)
  min_confidence: low

  # Restrict AI processing to matching paths / rules (glob patterns).
  # Empty means "no filter". (list[str], default: [])
  include_paths: []
  exclude_paths: []
  include_rules: []
  exclude_rules: []

  # ── Output & behavior ─────────────────────────────────────────
  # Set true to always run --fix in chk without the CLI flag.
  # (bool, default: false)
  default_fix: false

  # Auto-apply fixes without interactive review (use with caution).
  # (bool, default: false)
  auto_apply: false

  # Auto-apply deterministic style fixes (e.g. E501) in
  # non-interactive/JSON runs. (bool, default: true)
  auto_apply_safe_fixes: true

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

  # Preview mode: display AI fix suggestions without applying them.
  # (bool, default: false)
  dry_run: false

  # Post AI summaries and inline fix suggestions as PR review comments
  # when running in GitHub Actions. (bool, default: false)
  github_pr_comments: false

  # CI exit-code control. When true, an AI error (fail_on_ai_error) or
  # an unfixed/failed AI fix (fail_on_unfixed) contributes to a
  # non-zero exit code. (bool, default: false)
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

  # Cache entry time-to-live in seconds. (int >= 60, default: 3600)
  cache_ttl: 3600

  # Max cached entries before eviction. (int >= 1, default: 1000)
  cache_max_entries: 1000

  # ── Advanced / trust (leave off unless you understand the risk) ──
  # Pass "--trust" to the Cursor agent CLI. Security risk: the Cursor
  # provider can be fed prompt-injectable content (e.g. fork-PR diffs),
  # so keep this false outside fully trusted local workspaces.
  # (bool, default: false)
  cursor_trust_workspace: false

  # Let the git-native (CLI transport) review path delegate diff
  # retrieval to the provider instead of embedding a redacted diff.
  # Security risk: a delegated diff bypasses lintro's secret-redaction
  # choke point. (bool, default: false)
  review_allow_unredacted_git_native: false
```

### Config Defaults for CLI Flags

If you always want `--fix` without typing it, set the default in config:

```yaml
ai:
  enabled: true
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

## Environment Support

AI output adapts to the environment:

| Environment    | Rendering                                |
| -------------- | ---------------------------------------- |
| Terminal       | Rich Panels with color and structure     |
| GitHub Actions | `::group::` / `::endgroup::` collapsible |
| Markdown       | `<details>` / `</details>` collapsible   |
| JSON           | `ai_summary` and `ai_metadata` fields    |

### JSON Output

When using `--output-format json`, AI data is included in the output:

```json
{
  "results": [
    {
      "tool": "ruff",
      "issues": [...],
      "ai_metadata": {
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

To use AI features in Docker, pass your API key as an environment variable:

```bash
# Docker with AI features (Anthropic)
docker run --rm \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -v $(pwd):/code \
  ghcr.io/lgtm-hq/py-lintro:latest check .

# Docker with AI features (OpenAI)
docker run --rm \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -v $(pwd):/code \
  ghcr.io/lgtm-hq/py-lintro:latest check .
```

The Docker image includes the AI extras when built with `WITH_AI=true`:

```bash
docker build --build-arg WITH_AI=true -t lintro-ai .
```

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

Only `anthropic` and `openai` are supported. Check your `ai.provider` value.

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
- **Fix mode** (`--fix` or `lintro format`): The source of the file that has the issue,
  plus the issue message and error code. One API call per issue. How much of the file is
  sent depends on its size (see below).

### How much source code is sent in fix mode

The amount of code sent per issue is size-dependent, controlled by `FULL_FILE_THRESHOLD`
(500 lines) in `lintro/ai/fix_context.py`:

- **Files at or under 500 lines** — the **entire file** is sent as context, so the model
  can reason about the whole file when generating a fix.
- **Files over 500 lines** — only a context window around the issue line is sent
  (`context_lines`, default 15 lines before and after). The same windowing is used for a
  small file whose full contents would exceed the prompt token budget.
- **Token budget** — in all cases the prompt is capped by `max_prompt_tokens` (default
  12000). If the full file or the initial window would exceed it, the context window is
  progressively halved (down to a 3-line floor) until it fits.

So enabling fix mode can send whole source files to your configured provider. Secret
redaction and prompt-injection scanning (see below) are applied to every prompt
regardless of context size, and `max_prompt_tokens` bounds the maximum amount of code
that leaves your environment per request.

### What is NOT sent

- **Absolute paths** — all paths are made relative to the workspace root before sending
- **Other project files** — only files with reported issues are read; unrelated project
  files are never sent
- **Detected secrets** — recognized secret patterns are redacted from the file content,
  the issue message, and any context window before the prompt is sent

### Workspace boundary enforcement

AI fix suggestions are validated against the workspace root. Fixes targeting files
outside the workspace are rejected and never applied.

### Important notes

- AI suggestions can hallucinate incorrect fixes — always review before accepting
- See your provider's privacy policy for data retention:
  [Anthropic](https://www.anthropic.com/privacy),
  [OpenAI](https://openai.com/policies/privacy-policy)
