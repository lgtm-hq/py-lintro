# AI-Powered Features

Lintro includes optional AI-powered features that provide actionable insights and
interactive fix suggestions on top of standard linting results.

> **Requirements:** Python package extra + an API key.
>
> ```bash
> uv pip install 'lintro[ai]'
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

### AI Summary (default with `check`)

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

### AI Fixes in `fmt`

When running `lintro format`, tools auto-fix what they can. For remaining unfixable
issues, the AI generates fix suggestions and presents them interactively (same UX as
`--fix` in `chk`). After the session, a post-fix summary wraps up what was accomplished.

## Configuration

### Basic Setup

Add the `ai` section to `.lintro-config.yaml`:

```yaml
ai:
  enabled: true
  provider: anthropic # or "openai"
  # model: claude-sonnet-4-20250514  # uses provider default if omitted
  # api_key_env: ANTHROPIC_API_KEY   # uses provider default if omitted
```

### Full Configuration Reference

```yaml
ai:
  # Master toggle — all AI features are disabled when false
  enabled: true

  # Provider: "anthropic" or "openai"
  provider: anthropic

  # Model override (uses provider default if omitted)
  # model: claude-sonnet-4-20250514

  # Custom env var for API key (uses provider default if omitted)
  # api_key_env: MY_CUSTOM_KEY

  # Set true to always run --fix in chk without the CLI flag
  default_fix: false

  # Auto-apply fixes without interactive review (use with caution)
  auto_apply: false

  # Auto-apply deterministic style fixes (e.g. E501) in non-interactive/json runs
  auto_apply_safe_fixes: true

  # Max tokens per API request
  max_tokens: 4096

  # Max issues to generate AI fixes for per run
  max_fix_issues: 20

  # Concurrent API calls for fix (1-20)
  max_parallel_calls: 5

  # Max retries for transient API errors (0-10)
  max_retries: 2

  # API request timeout in seconds
  api_timeout: 60.0

  # Interactive mode: validate immediately after each accepted group
  validate_after_group: false

  # Show token count and cost estimate in output
  show_cost_estimate: true

  # Lines of surrounding context sent to AI for fix generation (1-100)
  context_lines: 15

  # Max lines above/below target for line-targeted fix search (1-50)
  fix_search_radius: 5

  # Retry backoff parameters
  retry_base_delay: 1.0 # Initial delay in seconds (min 0.1)
  retry_max_delay: 30.0 # Maximum delay in seconds (min 1.0)
  retry_backoff_factor: 2.0 # Multiplier per retry (min 1.0)
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

#### Anthropic (default)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

```yaml
ai:
  provider: anthropic
  # model: claude-sonnet-4-20250514  # default
```

#### OpenAI

```bash
export OPENAI_API_KEY=sk-...
```

```yaml
ai:
  provider: openai
  # model: gpt-4o  # default
```

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
  "results": [...],
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

1. **Limits** — `max_fix_issues` (default 20) caps API calls
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
- **Backoff:** 1s, 2s, 4s (capped at 30s)
- **Retried errors:** rate limits, transient provider errors
- **Not retried:** authentication errors (fail immediately)

AI failures never break the main linting flow. If the provider is unavailable, you get
your normal linting results with a one-line notice:

```text
AI: enhancement unavailable (API key not set)
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
│               │   validate-after-group: off      │
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

The Docker image includes the AI extras by default when built with `WITH_AI=true`:

```bash
docker build --build-arg WITH_AI=true -t lintro-ai .
```

## Troubleshooting

### "AI: enhancement unavailable"

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
- Reduce `max_fix_issues`

### High Costs

- Check `show_cost_estimate: true` is set to monitor usage
- Lower `max_fix_issues` (default 20) if fix generation is too expensive
- Avoid `default_fix: true` in config unless you want fixes every run
- Use `--fix` only when needed
