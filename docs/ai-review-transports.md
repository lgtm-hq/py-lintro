# AI review transports: API vs CLI

Lintro's AI review can run on two transports. Their timeouts, cost caps, failure modes,
and reported numbers mean different things. Configure them under `ai.transports.*`
(#1923).

## Decision table

| Dimension           | `api`                                       | `cli`                                                                                 |
| ------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------- |
| Credential          | the provider's API-key variable             | the provider's CLI login or CLI key (see the matrix below)                            |
| Billing             | Metered API spend                           | Subscription / OAuth session                                                          |
| Default timeout     | 60s (stream-sized per call)                 | 1800s (per CLI chunk)                                                                 |
| Cost cap field      | `ai.transports.api.max_cost_usd` (enforced) | `ai.transports.cli.max_cost_usd_advisory` (advisory)                                  |
| Legacy fallback     | `ai.api_timeout`, `ai.max_cost_usd`         | `ai.max_cost_usd` for the advisory only                                               |
| Auth mode recorded  | `api_key`                                   | `subscription`                                                                        |
| Cost basis recorded | `billed`                                    | `unpriceable`                                                                         |
| Typical CI failures | `insufficient_credits`, `auth_failed:key`   | `auth_failed:oauth_session`, `cli_version_drift`, `turn_timeout`, `killed_externally` |

The credential row is deliberately generic: each provider declares its own variables,
and lintro has no default provider. The full `(provider, transport)` matrix is below.
`docs/ai-features.md` also covers Claude's settings-file `apiKeyHelper` as a reachable
API credential.

**Bare-billing exception:** under `cli` with Anthropic, when
`ai.providers.anthropic.cli_bare` resolves to sending `--bare` (`auto` with a reachable
`ANTHROPIC_API_KEY`, or `always`, #1859), the call bills the API key — the run records
`auth_mode=api_key` and `cost_basis=estimated` instead of the subscription column above.

**Advisory means estimate-based, not unenforced:** the CLI advisory cap still stops the
run (finalizing a partial review) when _locally estimated_ cost reaches it. It is
"advisory" because subscription usage has no billed price — the estimate bounds work
done, not spend.

## Resolution

Effective settings = **transport profile → legacy scalar → built-in default**.

`lintro review` logs the resolved profile at start, for example:

```text
transport=cli auth=subscription timeout=1800 cap=advisory:$0.50 cost_basis=unpriceable
```

## Example config

```yaml
ai:
  # provider is required and has no default: anthropic | cursor | openai
  enabled: true
  provider: anthropic
  transport: cli
  transports:
    api:
      timeout: 60
      max_cost_usd: 0.50
    cli:
      timeout: 1800
      max_cost_usd_advisory: 0.50
```

## When to use which

- Prefer **`cli`** when you have a subscription session for the provider's agent binary
  and want CI without burning a metered API key. `cursor` serves `cli` only.
- Prefer **`api`** when you need enforced spend caps or streaming, on a provider that
  serves an API transport (`anthropic`, `openai`).

## Credentials and `LINTRO_CLI_BARE`

Providers are listed alphabetically; the order carries no recommendation.

| Provider    | Transport | Credential                                            |
| ----------- | --------- | ----------------------------------------------------- |
| `anthropic` | `api`     | `ANTHROPIC_API_KEY`                                   |
| `anthropic` | `cli`     | `CLAUDE_CODE_OAUTH_TOKEN` or a `claude` login session |
| `cursor`    | `cli`     | `CURSOR_API_KEY` or an `agent login` session          |
| `openai`    | `api`     | `OPENAI_API_KEY`                                      |
| `openai`    | `cli`     | `CODEX_API_KEY` or a `codex login` session            |

`cursor` serves no `api` transport, so it has no API row. `ai.api_key_env` renames the
variable for the `api` transport, and for the `cli` transport it is honoured wherever
the provider's auth probe declares `honors_api_key_env=True` — anthropic's and cursor's
CLIs both do, because each reads its own API-key variable directly. openai's declares
`honors_api_key_env=False`: the `codex` CLI always reads `CODEX_API_KEY`, never
`OPENAI_API_KEY`, so renaming the variable cannot reach it.

| Variable / setting                | Transport       | Role                                                   |
| --------------------------------- | --------------- | ------------------------------------------------------ |
| `ai.providers.anthropic.cli_bare` / `LINTRO_CLI_BARE` | cli | `auto` / `always` / `never` — whether to pass `--bare` |

`--bare` disables OAuth session login and authenticates only against an API key
(#1838/#1859). Dogfood CI pins `LINTRO_CLI_BARE=never` and keeps `ANTHROPIC_API_KEY` out
of scope so the subscription token is actually used.

The dogfood job can also run two further CLI lanes without an API key (#2472): provider
`anthropic` against a gateway such as z.ai's GLM Coding Plan by forwarding
`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` to the pinned `claude` binary, and
provider `openai` on a restored `~/.codex/auth.json` subscription session (org secret
`CODEX_AUTH_JSON`; `CODEX_API_KEY` would bill metered API credits, not the plan) — the
workflow resolves the Renovate-pinned codex version, installs it and decodes the secret
into the session file before the review step; with the secret unset the run fails
visibly at the credential gate.

## Reported numbers

Per-run sticky state records `transport`, `auth_mode`, and `cost_basis` (`billed` /
`estimated` / `unpriceable`). Under subscription CLI, any `~$` figure is unpriceable —
not a bill. Live `api` runs always record `billed` (usage is provider-reported);
`estimated` appears only for bare-billed CLI runs and for legacy records whose basis is
derived from `auth_mode` + locally estimated token usage.

See also `docs/ai-features.md` and the dogfood workflow helpers under `scripts/ci/`.
