# AI review transports: API vs CLI

Lintro's AI review can run on two transports. Their timeouts, cost caps, failure modes,
and reported numbers mean different things. Configure them under `ai.transports.*`
(#1923).

## Decision table

| Dimension           | `api`                                       | `cli`                                                                                 |
| ------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------- |
| Credential          | Provider API key — see matrix below         | Provider CLI login or CLI key — see matrix below                                      |
| Billing             | Metered API spend                           | Subscription / OAuth session                                                          |
| Default timeout     | 60s (stream-sized per call)                 | 900s (whole-turn)                                                                     |
| Cost cap field      | `ai.transports.api.max_cost_usd` (enforced) | `ai.transports.cli.max_cost_usd_advisory` (advisory)                                  |
| Legacy fallback     | `ai.api_timeout`, `ai.max_cost_usd`         | `ai.max_cost_usd` for the advisory only                                               |
| Auth mode recorded  | `api_key`                                   | `subscription`                                                                        |
| Cost basis recorded | `billed`                                    | `unpriceable`                                                                         |
| Typical CI failures | `insufficient_credits`, `auth_failed:key`   | `auth_failed:oauth_session`, `cli_version_drift`, `turn_timeout`, `killed_externally` |

Every `(provider, transport)` pair has its own credential. None is a default. Login
sessions also satisfy CLI transport — see `docs/ai-features.md` for `apiKeyHelper` and
per-binary login details.

**Bare-billing exception:** under `cli` with Anthropic, when `ai.cli_bare` resolves to
sending `--bare` (`auto` with a reachable `ANTHROPIC_API_KEY`, or `always`, #1859), the
call bills the API key — the run records `auth_mode=api_key` and `cost_basis=estimated`
instead of the subscription column above.

**Advisory means estimate-based, not unenforced:** the CLI advisory cap still stops the
run (finalizing a partial review) when _locally estimated_ cost reaches it. It is
"advisory" because subscription usage has no billed price — the estimate bounds work
done, not spend.

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
  provider: <anthropic|cursor|openai>
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

- Prefer **`cli`** when you have a local agent login or subscription and want to avoid
  metered API spend.
- Prefer **`api`** when you need enforced spend caps, streaming, or an SDK-backed
  provider.

## Credentials

| Provider    | Transport | Credential                |
| ----------- | --------- | ------------------------- |
| `anthropic` | `api`     | `ANTHROPIC_API_KEY`       |
| `anthropic` | `cli`     | `CLAUDE_CODE_OAUTH_TOKEN` |
| `cursor`    | `cli`     | `CURSOR_API_KEY`          |
| `openai`    | `api`     | `OPENAI_API_KEY`          |
| `openai`    | `cli`     | `CODEX_API_KEY`           |

`cursor` is CLI-only. CLI transport also accepts a login session for the matching binary
(`claude`, `agent`, `codex`) — see `docs/ai-features.md`.

### Anthropic `--bare` and `LINTRO_CLI_BARE`

| Variable / setting                | Transport                    | Role                                                   |
| --------------------------------- | ---------------------------- | ------------------------------------------------------ |
| `ANTHROPIC_API_KEY`               | api (required); cli optional | Metered API / bare-mode auth                           |
| `CLAUDE_CODE_OAUTH_TOKEN`         | cli                          | Subscription OAuth session for `claude`                |
| `ai.cli_bare` / `LINTRO_CLI_BARE` | cli                          | `auto` / `always` / `never` — whether to pass `--bare` |

`--bare` disables OAuth session login and authenticates only against an API key
(#1838/#1859). When dogfooding Anthropic CLI, pin `LINTRO_CLI_BARE=never` and keep
`ANTHROPIC_API_KEY` out of scope so the subscription token is actually used.

## Reported numbers

Per-run sticky state records `transport`, `auth_mode`, and `cost_basis` (`billed` /
`estimated` / `unpriceable`). Under subscription CLI, any `~$` figure is unpriceable —
not a bill. Live `api` runs always record `billed` (usage is provider-reported);
`estimated` appears only for bare-billed CLI runs and for legacy records whose basis is
derived from `auth_mode` + locally estimated token usage.

See also `docs/ai-features.md` and the dogfood workflow helpers under `scripts/ci/`.
