# AI review transports: API vs CLI

Lintro's AI review can run on two transports. Their timeouts, cost caps, failure modes,
and reported numbers mean different things. Configure them under `ai.transports.*`
(#1923).

## Decision table

| Dimension          | `api`                                                               | `cli`                                                                           |
| ------------------ | ------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Invocation         | Provider SDK; inference-only                                        | Installed provider agent binary                                                 |
| Providers          | Anthropic, OpenAI; Cursor with the separate `lintro[cursor]` extra  | Anthropic (`claude`), OpenAI (`codex`), Cursor (`agent`)                        |
| Credential         | Provider API-key variable                                           | Provider CLI login/session or CLI-supported key                                 |
| Default timeout    | 60 s profile; Cursor SDK has a 180 s floor                          | 1800 s per CLI chunk                                                            |
| Profile cap        | `ai.transports.api.max_cost_usd`                                    | `ai.transports.cli.max_cost_usd_advisory`                                       |
| Billing/provenance | First-party SDK profiles resolve as `billed`; gateways as `unknown` | Depends on actual credential and endpoint: metered key, subscription or gateway |

The profile field's name alone does not determine whether a review stops. Keys passed
through a CLI can still bill an API account. Subscription list-price estimates are not
invoices. Cursor's subscription pricing metadata uses zero rates; this release
nevertheless resolves its first-party SDK profile as `billed`. Use the recorded run
basis, not a transport-wide billing promise.

## Resolution

Timeout and cost settings resolve separately:

- API timeout: active API profile → `ai.api_timeout` → 60 seconds.
- CLI timeout: active CLI profile → 1800 seconds; it ignores `ai.api_timeout`.
- Cost bound: active transport profile → `ai.max_cost_usd` → unset.

`lintro review --timeout` overrides the resolved timeout. Cursor SDK requests retain
their 180-second minimum. See the
[operational profile reference](/docs/ai/ai-features/configuration-reference/#find-a-setting)
for profile fields and null fallbacks.

AI provider has no default; explicitly configure the transport too (doctor reports it
unset).

For `lintro review`, cap enforcement is **cap source × resolved cost basis**:

- A flag or environment cap (`--max-cost-usd` / `LINTRO_AI_MAX_COST_USD`) enforces on
  every basis, including subscription estimates.
- A committed configuration cap enforces on `billed`, `estimated` and `unknown`; it is
  display-only on `unpriceable` and `list_price_shadow` subscription bases.
- No cap means uncapped. `uncapped` explicitly clears an invocation overlay; overlay `0`
  is rejected, while YAML `0` is a real zero-dollar cap on enforced bases.
- A cap is checked against accounted/estimated usage and can overshoot by an in-flight
  call. It is not a provider billing limit.

The AI lint sessions behind check/format build their budget from `ai.max_cost_usd`
directly, so a YAML cap also stops those sessions on subscription estimates. Do not
infer review behavior from the lint session.

These rules come from
[cost_cap.py](https://github.com/lgtm-hq/py-lintro/blob/b9fd8c1740c1cfc209224d8257b51bcfed6ee778/lintro/ai/review/cost_cap.py),
[transport.py](https://github.com/lgtm-hq/py-lintro/blob/b9fd8c1740c1cfc209224d8257b51bcfed6ee778/lintro/ai/transport.py)
and
[orchestrator.py](https://github.com/lgtm-hq/py-lintro/blob/b9fd8c1740c1cfc209224d8257b51bcfed6ee778/lintro/ai/orchestrator.py).

## Example config

```yaml
ai:
  enabled: true
  lint: false
  review: true
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

On a subscription CLI basis, that committed review cap is display-only. To enforce an
estimate-based work ceiling for an individual review, use an invocation cap, for example
`lintro review --uncommitted --max-cost-usd 0.50` with an already configured provider
and credentials. This example can make paid calls; it is not a browser demo.

## When to use which

Use `cli` with an authenticated local agent binary, or `api` for an SDK request without
that binary. API transport is inference-only. Cursor SDK is optional and beta, accepts
no endpoint override, and has no hard output-token limit; `max_tokens` is a prompt
instruction there. Neither transport is credential-free or guaranteed free of charge.

## Credentials and `LINTRO_CLI_BARE`

Providers are listed alphabetically; the order carries no recommendation.

| Provider    | Transport | Credential                                                                                               |
| ----------- | --------- | -------------------------------------------------------------------------------------------------------- |
| `anthropic` | `api`     | `ANTHROPIC_API_KEY`                                                                                      |
| `anthropic` | `cli`     | `CLAUDE_CODE_OAUTH_TOKEN`, a `claude` login session, `ANTHROPIC_API_KEY`, or a configured `apiKeyHelper` |
| `cursor`    | `api`     | `CURSOR_API_KEY` with `lintro[cursor]`                                                                   |
| `cursor`    | `cli`     | `CURSOR_API_KEY` or an `agent login` session                                                             |
| `openai`    | `api`     | `OPENAI_API_KEY`                                                                                         |
| `openai`    | `cli`     | `CODEX_API_KEY` or a `codex login` session                                                               |

`ai.api_key_env` renames the variable for the `api` transport. On the `cli` transport
the binary reads whatever variable it is built to read, and `honors_api_key_env` on the
provider's auth probe records only whether `lintro doctor` accepts the renamed variable
as proof of a credential:

- `cursor` declares `True` and means it — the `agent` CLI reads `CURSOR_API_KEY` itself.
- `openai` declares `False`: the `codex` CLI always reads `CODEX_API_KEY`, never
  `OPENAI_API_KEY`, so renaming cannot reach it.
- `anthropic` declares `True` and lintro makes it so: the `claude` binary reads only the
  literal `ANTHROPIC_API_KEY`, so the transport exports the renamed variable's value
  under that name into the subprocess environment, for that process only
  (`lintro/ai/providers/claude_auth.py`). When both are set, the renamed variable wins,
  as it does on the `api` transport. The bare-mode decision reads the same child
  environment, so a renamed key still selects `--bare` under `auto`. Before this landed
  a renamed key never reached the binary, so a subscription user who keeps one exported
  (say for an `api` profile) and runs the `cli` transport now bills that key under
  `auto`; set `cli_bare: never` or `LINTRO_CLI_BARE=never` to keep the login session.

| Variable / setting                                    | Transport | Role                                                   |
| ----------------------------------------------------- | --------- | ------------------------------------------------------ |
| `ai.providers.anthropic.cli_bare` / `LINTRO_CLI_BARE` | cli       | `auto` / `always` / `never` — whether to pass `--bare` |

`--bare` disables OAuth session login and authenticates only against an API key
(#1838/#1859). Dogfood CI pins `LINTRO_CLI_BARE=never` and keeps `ANTHROPIC_API_KEY` out
of scope so the subscription token is actually used.

Where the dogfood review goes is a named **lane**: a row in the committed manifest
`scripts/ci/ai_review_lanes.json`, and nowhere else (ADR-0011 decision A, #2869/#2870).
The repository variable `LINTRO_AI_PROVIDER` selects the row (`anthropic`, `openai`,
`cursor`, `zai` or `kimi`; unset means the manifest's `chain[0]`; anything else is red
before a secret exists), and `LINTRO_AI_MODEL` overrides the row's default model. A
secret-free `lane` job in `ai-review.yml` reads those two variables, resolves the chain
with `scripts/ci/resolve_review_lanes.py` at `github.workflow_sha` and publishes every
row; the review job's egress is the union of the chain's hosts, each credential is
forwarded under its secret name by one literal expression gated on chain membership, and
`scripts/ci/run-ai-review.sh` binds the attempt through `scripts/ci/review_lane_env.py`:
provider, transport and model are exported from the row, exactly one credential is in
scope under the variable its CLI reads (the z.ai and Kimi tokens fill
`ANTHROPIC_AUTH_TOKEN`; the Codex session is decoded from `CODEX_AUTH_JSON` into
`~/.codex/auth.json` for the attempt and deleted at its end), the provider's base-URL
override is exported to the row's endpoint off-host and must be unset on-host, and a
competing credential, an override naming another host, or an effective `ai.*` config
that disagrees with the row refuses the run before any provider call. An absent
credential is red with `status=no-credential`, never a fallback. No variable supplies an
endpoint, a credential binding or an egress host: a selector can only name a declared
row. The declared rows and their default models are `anthropic` (`claude-opus-5-5`, the
default lane), `openai` (the Codex session, `gpt-6.1-sol`), `cursor`
(`cursor-grok-4.6-high`), `zai` (`glm-5.3-flash`) and `kimi` (`kimi-k3`), the last two
non-first-party gateways. Switching lanes is a change to the variable and reaches every
open PR on its next run; a rerun keeps its run's inputs, so after a switch the recovery
step for a red PR is an `@lintro review` comment, which starts a new run. After a review
is produced, `scripts/ci/classify_review_outcome.py` reads this round's run record and
applies ADR-0011 decision C through `scripts/ci/review_lane_verdict.py` (#2871): a
recorded lane outside the chain or an endpoint off that lane's host is red with
`status=endpoint-mismatch`; a first-party lane that answered with another model is red
with `status=model-mismatch`, and one whose transport declares model usage but reported
none is red with `status=model-unreported`; a review whose run record cannot vouch for
it (none persisted by this run and attempt, a record missing an endpoint or carrying a
verification state its lane cannot produce, or a recorded lane with no published
manifest row) is red with `status=provenance-unrecorded`, except that a partial review
(incomplete, budget-stopped or degraded) keeps its own red reason and carries the
missing provenance as a `::warning::`; a fallback lane answering, or a gateway's
unverifiable model, is a `::warning::` that leaves the verdict alone. An authentication
failure is labelled by the credential kind the lane bound, so a gateway 401 is never
blamed on the Claude session.

The chain is walked (ADR-0011 decision D, #2872). When an attempt exits with the review
error status and its envelope names `auth_failed`, `insufficient_credits` or
`quota_exceeded`, its `completed_chunks` is zero (a missing count refuses the walk) and
no checkpoint part was written during the attempt, the wrapper takes back that lane's
credential, binds the next lane in `chain` with only its own, and tries again;
`scripts/ci/review_chain.py` makes that decision from the envelope's kind, never from
its `provider_unavailable` flag. A rate limit, a server error or a timeout stays with
the existing retry, and a lane that fails after a chunk completed is never retried
elsewhere: the partial round is persisted for a same-lane resume and the check is red
with `status=provider-unavailable`. A fallback round is green; the sticky leads with a
line naming the dead lane, its error kind and the lane that answered, the job summary
carries the same as a `::warning::`, and one issue labelled `ai-review-lane-down` is
opened or updated per dead lane through the App token. When a chain of two or more lanes
has every lane fail before any chunk completed, the check is red with
`status=lanes-exhausted`. A fallback lane that cannot be bound (its credential is
absent, or the gate refuses it) is recorded as `unbound` and the next lane is tried; a
cancel ends the walk at once. Two edges to know: the walk keys on the envelope's `kind`,
so a thin envelope the classifier upgrades to `auth_failed` from its prose does not
walk, and the first attempt's error sticky stays on the PR until the fallback attempt
replaces it.

The dogfood `openai` lane always sends its row's model (`gpt-6.1-sol`, or the
`LINTRO_AI_MODEL` override). Outside a lane, on the openai `cli` transport lintro sends
**no** `--model` unless one is configured (#2537). The two codex credentials do not
accept the same models: an API key reaches the OpenAI API catalogue and still gets the
provider default (`gpt-4o`), while a ChatGPT-plan session reaches only that plan's
models and rejects an API-catalogue name outright — the whole call fails with
`The 'gpt-4o' model is not supported when using Codex with a ChatGPT account`. The plan
catalogue is per-account and renames often, so lintro defers to codex's own default
rather than pinning a slug that would break on the next rename. Set `ai.model` (or
`LINTRO_AI_MODEL`) to override; an explicit model is always honoured. The credential is
detected from `$CODEX_HOME/auth.json` — `CODEX_HOME`, not `$HOME`, because CI restores
the session outside the home directory. The run record's `credential` follows the same
precedence: `CODEX_API_KEY` records `api_key`, a session file carrying an embedded key
records `api_key`, a plan session records `codex_oauth`, and `OPENAI_API_KEY` alone
records nothing, because codex never reads it.

## CI support levels per transport

What CI proves about each transport, and how often (#2600):

| Check                                          | Transport | Runs                          | Spends quota |
| ---------------------------------------------- | --------- | ----------------------------- | ------------ |
| CLI flag surface (Tier 1)                      | `cli`     | every PR / push / merge group | no           |
| CLI output replay fixtures                     | `cli`     | every PR / push / merge group | no           |
| CLI invocation smoke (Tier 2, **manual only**) | `cli`     | `workflow_dispatch` only      | yes          |
| Provider API smoke                             | `api`     | weekly + dispatch             | yes (cents)  |

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

**Unfunded probes can be parked explicitly** (#2837). A row's optional `parked` field
holds a non-empty, single-line reason. Parked rows are still validated but excluded from
the matrix; every run lists their reasons in notices and the job summary. They make no
provider call and publish no commit status. Direct `--provider` selection exits 2 with
`outcome=parked`, never success or pending. If all rows are parked, the provider job is
skipped without a dummy success. To unpark after funding returns, delete the row's
`parked` flag. Keep #2837 open until the funded probes have been verified, including
Z.ai's earlier wrong-answer case. `anthropic-api` remains live; `openai-api`,
`kimi-api`, `zai-api` and `zai-anthropic-api` are parked by the owner's decision.

**Every dogfood lane names its smoke row and how far it reaches** (#2873). A row of the
lanes manifest carries `smoke_row`, the smoke table row that stands for the lane, and
`smoke_covers`, what that weekly call proves. `route`: the row sends the lane's own
secret down the lane's own path, so a dead token and a dead path both go red (`zai` via
`zai-anthropic-api`, not the OpenAI-protocol `zai-api` route that shares its token;
`kimi`). `host`: the lane's credential is a subscription session the raw API cannot
take, so the row sends the provider's metered key to the host the CLI calls and proves
the host answers, not that the session is alive (`anthropic`). `provider`: the session's
own route is one no API smoke can reach, so the row proves only that the provider's API
answers (`openai`: the Codex session calls the ChatGPT backend, while `openai-api` calls
`api.openai.com`). An expired session is reported by the dogfood's own `auth_failed`
verdict on the next review, never by the smoke. A lane no smoke can reach at all
(`cursor`, whose optional SDK is not one of this raw API smoke matrix’s protocols)
carries `smoke_row: null` and a `smoke_gap` saying why.
`tests/scripts/ci/test_smoke_rows_match_lanes.py` pins the metered key and the level
each session lane may claim, and fails when the two files drift apart. Parking retains
those bindings and validates their contracts, but suspends live coverage: `smoke_covers`
describes what an active probe proves, not a parked row.

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
`estimated` / `unpriceable` / `list_price_shadow` / `unknown`). Under subscription CLI a
`~$` figure is not a bill: it is recorded as `list_price_shadow` when the CLI envelope
reported a list-price figure for a first-party endpoint, and as `unpriceable` when it
reported none. A run pointed at a non-first-party endpoint records `unknown`: the
envelope's figure is priced on the wrong table and is never shown as dollars. On both
shadow bases the record's `cost` is zero and the figure lives in `cost_shadow`, which
still settles enforced invocation caps and the PR budget according to the source/basis
policy above (ADR-0011, decision B). First-party `api` profiles resolve as `billed`;
off-provider endpoints resolve as `unknown`. This is a provenance classification, not
proof of an invoice; `estimated` appears only for CLI runs on a metered key
(bare-billed, `CODEX_API_KEY`, a codex session with an embedded key, or
`ANTHROPIC_API_KEY` under `cli_bare: never`) and for legacy records whose basis is
derived from `auth_mode` + locally estimated token usage.

Every surface that names the model names the endpoint beside it (#2868): the sticky's
`This run` row, the badges, the run-by-run history, the review body footer and the
terminal header read ``requested `model` (source) · verified on `host` · lane `x` ``, or
``answered via `host` (third-party gateway), model unverifiable``; stored history rows
carry no `(source)`. When the state is `mismatch`, `unverifiable` or `unreported`, a
line at the top of the sticky, outside every collapsible, names the lane, the endpoint
and the state; a `legacy` round never tops a board (ADR-0011 C). Every dollar figure
carries its basis: `unknown` reads `n/a (third-party gateway)`, never a figure and never
zero; a list-price shadow is labelled a shadow; a round from before ADR-0011 is labelled
`predates ADR-0011`; and the history heading discloses how many rounds contributed no
priced cost.

Each run also records where its requests went (#2867): `endpoint` (host only),
`first_party`, `credential` (`claude_oauth`, `api_key`, `zai_token`, `kimi_token`,
`codex_oauth`, `cursor_api_key`), `reported_models` (the Claude CLI envelope's
`modelUsage`), `lane`, and `model_verification` (`verified`, `mismatch`, `unverifiable`,
`unreported`; a record from before these fields reads as `legacy`). `auth_mode` follows
`credential`, so a gateway token reads `gateway_token`, not `subscription`. In CI the
review-lanes wrapper exports the resolved lane row as `LINTRO_REVIEW_LANE`,
`LINTRO_REVIEW_ENDPOINT` and `LINTRO_REVIEW_FIRST_PARTY`, and those win; no
allowed-model list travels, so on a lane `model_verification` accepts the requested
model alone (a first-party call that reports another model is `mismatch` and its
coverage is not reused). Outside CI the provider's base-URL override
(`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, or the Claude settings files' `env` block) and
the provider's own host decide. The credential kinds add `api_key` (a metered vendor
key, or the API transport) and `unknown` to the five lane bindings so a local run can be
described too. The Claude credential follows Claude Code's documented precedence: the
bearer token, then the API key or helper, then the OAuth token, which bare mode never
reads. A run off the provider's host prices its shadow figure on
`LINTRO_REVIEW_SHADOW_PRICE_MODEL`, or on the requested model's own table when no lane
row names one.

Reuse follows provenance (ADR-0011, decision B): a stored round's coverage is inherited
only when the round is not `legacy` or `mismatch`, is not `unreported` on a lane whose
transport reports model usage (`LINTRO_REVIEW_REPORTS_MODEL_USAGE`, defaulting to true
for the Claude CLI and every API transport), and its `lane`, `endpoint` and, on a
first-party lane, its reported models still match the current lane. Coverage that no
stored run record can place on a lane is not inherited. A mid-run checkpoint writes a
partial run record carrying the round's lane and endpoint, so a round killed after a
checkpoint leaves coverage the gate can place. Outside a lane row the allowed models are
the default model, its declared fallbacks and the custom agents' `model` overrides; a
local Claude CLI run whose envelope names an auxiliary model outside that set reads
`mismatch`, as the ADR requires.

Silent calls record their requested models separately in `unreported_models`, including
agent overrides. Checkpoints and their replacements retain those claims when coverage is
inherited; a verified call on resume cannot promote silent coverage to `verified`. Every
silent claim must still satisfy the current allowed-model list. Mixed verified and
unverifiable sources cannot establish a shared verification state, so carried provenance
fails closed.

Old blank-state artifacts and mixed `unreported` artifacts missing their silent claims
need one re-review. A complete re-review replaces those old claims with current
evidence. An omitted `unreported_models` key means an older record: only a purely silent
record can fall back to its recorded requested model. Explicit null or malformed claims
stay disallowed instead of disappearing; a mixed record's default model cannot
reconstruct its missing silent requests.

See also `docs/ai-features.md` and the dogfood workflow helpers under `scripts/ci/`.
