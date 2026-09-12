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

| Provider    | Transport | Credential                                                                                               |
| ----------- | --------- | -------------------------------------------------------------------------------------------------------- |
| `anthropic` | `api`     | `ANTHROPIC_API_KEY`                                                                                      |
| `anthropic` | `cli`     | `CLAUDE_CODE_OAUTH_TOKEN`, a `claude` login session, `ANTHROPIC_API_KEY`, or a configured `apiKeyHelper` |
| `cursor`    | `cli`     | `CURSOR_API_KEY` or an `agent login` session                                                             |
| `openai`    | `api`     | `OPENAI_API_KEY`                                                                                         |
| `openai`    | `cli`     | `CODEX_API_KEY` or a `codex login` session                                                               |

`cursor` serves no `api` transport, so it has no API row. `ai.api_key_env` renames the
variable for the `api` transport. On the `cli` transport the binary reads whatever
variable it is built to read, and `honors_api_key_env` on the provider's auth probe
records only whether `lintro doctor` accepts the renamed variable as proof of a
credential:

- `cursor` declares `True` and means it — the `agent` CLI reads `CURSOR_API_KEY` itself.
- `openai` declares `False`: the `codex` CLI always reads `CODEX_API_KEY`, never
  `OPENAI_API_KEY`, so renaming cannot reach it.
- `anthropic` declares `True`, but the `claude` binary reads only the literal
  `ANTHROPIC_API_KEY` (`lintro/ai/providers/claude_auth.py`), and lintro does not
  re-export a renamed variable into it. Doctor therefore reports OK for a credential the
  CLI cannot see. That mismatch predates the provider refactor and is tracked by #2449;
  until it lands, rename `ai.api_key_env` for anthropic's `api` transport only.

| Variable / setting                                    | Transport | Role                                                   |
| ----------------------------------------------------- | --------- | ------------------------------------------------------ |
| `ai.providers.anthropic.cli_bare` / `LINTRO_CLI_BARE` | cli       | `auto` / `always` / `never` — whether to pass `--bare` |

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

On that lane lintro sends **no** `--model` unless one is configured (#2537). The two
codex credentials do not accept the same models: an API key reaches the OpenAI API
catalogue and still gets the provider default (`gpt-4o`), while a ChatGPT-plan session
reaches only that plan's models and rejects an API-catalogue name outright — the whole
call fails with
`The 'gpt-4o' model is not supported when using Codex with a ChatGPT account`. The plan
catalogue is per-account and renames often, so lintro defers to codex's own default
rather than pinning a slug that would break on the next rename. Set `ai.model` (or
`LINTRO_AI_MODEL`) to override; an explicit model is always honoured. The credential is
detected from `$CODEX_HOME/auth.json` — `CODEX_HOME`, not `$HOME`, because CI restores
the session outside the home directory.

## CI support levels per transport

What CI proves about each transport, and how often (#2600):

| Check                                          | Transport | Runs                     | Spends quota |
| ---------------------------------------------- | --------- | ------------------------ | ------------ |
| CLI flag surface (Tier 1)                      | `cli`     | every PR / push          | no           |
| CLI output replay fixtures                     | `cli`     | every PR / push          | no           |
| CLI invocation smoke (Tier 2, **manual only**) | `cli`     | `workflow_dispatch` only | yes          |
| Provider API smoke                             | `api`     | weekly + dispatch        | yes (cents)  |

**The CLI invocation smoke is manual-only.** It used to run weekly and failed every
Monday from 2026-08-10 on an exhausted prepaid balance without reaching anyone: it gates
nothing, and its credentials are subscription- and session-bound, so they cannot be kept
green by design. Drift between manual runs is accepted; run it from the Actions tab
(`AI - CLI Contract Tests` → Run workflow) after a CLI pin bump or before trusting a
CLI-transport change.

**The weekly live signal is the API smoke**
(`.github/workflows/ai-provider-api-smoke.yml`), one job per funded provider from
`scripts/ci/ai_provider_smoke/providers.json`. Each row declares a protocol (`anthropic`
or `openai`), base URL, model, and `key_env` — the name of the environment variable the
repository secret is injected into, never a credential value — so a gateway is a row
rather than code. A failure — credit exhaustion included — posts a red
`ai-provider-smoke/<row>` commit status on `main` and files the error text on the
deduplicated tracker issue under the `ai-provider-smoke` label; that error text is
dropped whole if it echoes the credential and otherwise redacted, so a gateway quoting
the key back cannot leak it onto an issue. A row whose credential variable is empty
reports a pending status and a skip notice, never a pass.

**Parser drift is caught for free.**
`tests/fixtures/ai/cli_replay/<cli>/<version>.jsonl` holds one stdout capture per agent
CLI at the pinned version, and the Tier 1 replay test parses it with the real transport
parser on every PR. The committed captures are hand-authored to the schema each parser
documents, so until someone runs `scripts/ci/record_cli_fixture.sh <cli>` inside the
`lintro-ai-tools` image (it needs the CLIs and a credential) the test guards our parsers
rather than proving vendor output. Re-record whenever a pin in
`docker/ai-tools.Dockerfile` moves.

## Reported numbers

Per-run sticky state records `transport`, `auth_mode`, and `cost_basis` (`billed` /
`estimated` / `unpriceable`). Under subscription CLI, any `~$` figure is unpriceable —
not a bill. Live `api` runs always record `billed` (usage is provider-reported);
`estimated` appears only for bare-billed CLI runs and for legacy records whose basis is
derived from `auth_mode` + locally estimated token usage.

See also `docs/ai-features.md` and the dogfood workflow helpers under `scripts/ci/`.
