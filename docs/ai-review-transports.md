# AI review transports: API vs CLI

Lintro's AI review can run on two transports. Their timeouts, cost caps,
failure modes, and reported numbers mean different things. Configure them
under `ai.transports.*` (#1923).

## Decision table

| Dimension | `api` | `cli` |
| --- | --- | --- |
| Credential | `ANTHROPIC_API_KEY` (or provider key) | `CLAUDE_CODE_OAUTH_TOKEN` / `claude` login |
| Billing | Metered API spend | Subscription / OAuth session |
| Default timeout | 60s (stream-sized per call) | 900s (whole-turn) |
| Cost cap field | `ai.transports.api.max_cost_usd` (enforced) | `ai.transports.cli.max_cost_usd_advisory` (advisory) |
| Legacy fallback | `ai.api_timeout`, `ai.max_cost_usd` | `ai.max_cost_usd` for the advisory only |
| Auth mode recorded | `api_key` | `subscription` |
| Cost basis recorded | `billed` | `unpriceable` |
| Typical CI failures | `insufficient_credits`, `auth_failed:key` | `auth_failed:oauth_session`, `cli_version_drift`, `turn_timeout`, `killed_externally` |

## Resolution

Effective settings = **transport profile → legacy scalar → built-in default**.

`lintro review` logs the resolved profile at start, for example:

```text
transport=cli auth=subscription timeout=900 cap=advisory:$0.50 cost_basis=unpriceable
```

## Example config

```yaml
ai:
  enabled: true
  provider: anthropic
  transport: cli
  transports:
    api:
      timeout: 60
      max_cost_usd: 0.50
    cli:
      timeout: 900
      max_cost_usd_advisory: 0.50
```

## When to use which

- Prefer **`cli`** when you have a Claude Code / subscription OAuth session and
  want dogfood CI without burning a metered API key.
- Prefer **`api`** when you need enforced spend caps, streaming, or non-Claude
  providers with API keys.

## Credentials and `LINTRO_CLI_BARE`

| Variable / setting | Transport | Role |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | api (required); cli optional | Metered API / bare-mode auth |
| `CLAUDE_CODE_OAUTH_TOKEN` | cli | Subscription OAuth session for `claude` |
| `ai.cli_bare` / `LINTRO_CLI_BARE` | cli | `auto` / `always` / `never` — whether to pass `--bare` |

`--bare` disables OAuth session login and authenticates only against an API key
(#1838/#1859). Dogfood CI pins `LINTRO_CLI_BARE=never` and keeps
`ANTHROPIC_API_KEY` out of scope so the subscription token is actually used.

## Reported numbers

Per-run sticky state records `transport`, `auth_mode`, and `cost_basis`
(`billed` / `estimated` / `unpriceable`). Under subscription CLI, any `~$`
figure is unpriceable — not a bill.

See also `docs/ai-features.md` and the dogfood workflow helpers under
`scripts/ci/`.
