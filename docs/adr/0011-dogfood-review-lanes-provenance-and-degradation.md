# ADR-0011: Dogfood review lanes, endpoint provenance and degradation

## Status

Accepted, implementation pending under epic
[#2874](https://github.com/lgtm-hq/py-lintro/issues/2874). The decisions are the
acceptance criteria of that epic's issues; each "Delivered by" line names the issue that
lands it, and the interim measure in Consequences applies until
[#2870](https://github.com/lgtm-hq/py-lintro/issues/2870) merges.

## Context

The `AI Review - Dogfood` check (`.github/workflows/ai-review.yml`) reviews every pull
request with `lintro review` itself. Which provider answers, through which endpoint,
with which credential, is decided by the workflow's environment. ADR-0006 to ADR-0010
fix the effective config, the resume model, the architecture invariants, the provider
plugin seam and the review protocol. None of them says where a request may go, how the
run must report where it went, or what happens when the configured provider is
unavailable.

[#2473](https://github.com/lgtm-hq/py-lintro/pull/2473) (merged 2026-09-08) added a
gateway lane for the Anthropic provider: when the `vars` context carries a non-empty
`ZAI_BASE_URL`, the review step blanks `CLAUDE_CODE_OAUTH_TOKEN`, sets
`ANTHROPIC_BASE_URL` to that value, forwards the org secret `ZAI_AUTH_TOKEN` as
`ANTHROPIC_AUTH_TOKEN`, and widens egress to `api.z.ai:443`. The `claude` CLI then talks
to Z.ai's Anthropic-protocol endpoint and a GLM model answers.

On 2026-09-09 `ZAI_BASE_URL` was created as an **organisation** variable visible to all
repositories. The workflow header documents it as a repository variable. GitHub's `vars`
context merges organisation and repository variables and does not expose which scope a
value came from, and GitHub rejects an empty variable value, so a repository could not
neutralise the inherited one. From that day every dogfood review on py-lintro ran
through Z.ai. Nothing in the repository recorded that a lane switch had happened:

- The sticky comment printed the **configured** model with an `(env)` marker
  (`lintro/ai/resolved_ai_config.py`, `lintro/ai/review/github_badges.py`,
  `lintro/ai/review/github_notes.py`), so the header said `claude-opus-5-5` while GLM
  answered.
- `auth_mode` is derived from the CLI `--bare` policy (`lintro/ai/transport.py`), so it
  said `subscription` while a gateway token was in use.
- The provider sets `AIResponse.model` to the model it was asked for
  (`lintro/ai/providers/anthropic/provider.py`). The CLI envelope's `modelUsage` block,
  which names the model that actually answered with its `canonicalModel` and `provider`,
  is not read (fixture `tests/fixtures/ai/cli_replay/claude/2.1.286.jsonl`). The
  `provider` value in that block is what the CLI reports, not evidence of which endpoint
  served the request; decision B takes `first_party` from the manifest only and stores
  the envelope's `provider` for information.
- Cost was the CLI's `total_cost_usd`, priced on Anthropic's list price for a request
  Anthropic never served.
- The weekly provider smoke's `zai-api` row exercises Z.ai's OpenAI-protocol endpoint
  (`scripts/ci/ai_provider_smoke/providers.json`), not the `/api/anthropic` route the
  dogfood used, so the smoke could not detect a dead gateway.
- `RunIdentity` (`lintro/ai/review/models/run_identity.py`) has no endpoint field, so a
  run record cannot say where its requests went.

The incident surfaced on 2026-10-12 when the Z.ai balance ran out and every review went
red with `insufficient_credits`, holding eleven Renovate pull requests. The workflow
header states that "a billing condition is loud but never blocks a merge", and
`scripts/ci/run-ai-review.sh` that "the check is deliberately NOT required, so a billing
condition is loud without blocking a merge". The check is indeed not a required status
on `main`, but the owner merges under a no-failing-checks rule, so in practice a red
dogfood review does block. The premise that red is free is false and has been false
since the no-silent-skip change in
[#1826](https://github.com/lgtm-hq/py-lintro/issues/1826).

Two independent design reviews (Claude Opus 5.5 and GPT-6.1 Sol, 2026-10-13) converged
on the same shape. The owner decided the four remaining points the same day:

1. Unblock by rescoping the organisation variable's visibility, not by code.
2. A repository variable selects a declared lane; it never supplies an endpoint. Revised
   on review of this ADR: the `vars` context cannot prove a variable is
   repository-scoped, so a selector variable would be inheritable in exactly the way
   `ZAI_BASE_URL` was. The selection therefore lives in the manifest (decision A).
3. Degradation follows a declared, ordered fallback chain.
4. The Z.ai lane is kept as an explicit opt-in, not removed.

Epic [#2874](https://github.com/lgtm-hq/py-lintro/issues/2874) carries the work; this
ADR is its design record and the acceptance criterion for each sub-issue.

## Decision

### A. Lane invariant

A **lane** is a named row in the committed manifest `scripts/ci/ai_review_lanes.json`. A
row declares the provider, transport, endpoint, whether the endpoint is first-party for
that provider, whether the transport reports model usage (`reports_model_usage`), the
name of the credential it binds and its form (an environment variable, or a file the
wrapper materialises: the Codex session is `$HOME/.codex/auth.json`, decoded from
`CODEX_AUTH_JSON`), the exact egress hosts it needs, the default model, the allowed
models, and the cost basis its figures carry.

- The manifest is the **only** place an endpoint, a credential binding or an egress host
  may be defined. No workflow expression derives any of them from the `vars` context.
- The manifest's ordered `chain` of lane ids is the only routing input: its first entry
  is the active lane, and the rest are the fallback order of decision D. There is no
  separate selector field, so the active lane and the chain cannot disagree. No
  variable, at any scope, selects, enables or shapes a lane. Switching lanes is a
  reviewed one-line diff to the manifest. It takes effect for every open pull request on
  the next event that starts the workflow, because the resolver reads the manifest at
  `github.workflow_sha`, the `main` commit that supplied the workflow definition
  (`ai-review.yml` runs under `pull_request_target` and `issue_comment`, and every
  dogfood PR targets `main`), and not at `pull_request.base.sha`, which is the base as
  of the PR's last sync and can lag `main`. A rerun of an existing run keeps that run's
  `workflow_sha` and therefore its manifest, so after an emergency switch the recovery
  step for a red PR is an `@lintro review` comment, which starts a new run, not a rerun.
  An empty chain, an entry that does not name a declared row, a row that declares a
  wildcard host, a chain that mixes `first_party` values or has more than one entry
  behind a non-first-party first entry (decision D), or a row that needs a
  `shadow_price_model` and declares none (decision B) fails closed in the resolver
  before a secret is exposed, with the allowed lane list in the error.
- The existing `vars.LINTRO_AI_PROVIDER`, `vars.LINTRO_AI_TRANSPORT` and
  `vars.LINTRO_AI_MODEL` reads in the dogfood jobs are removed; provider, transport and
  model come from the lane row. Budget overlays remain variables because they shape
  spend, not routing. A structural test asserts that no job in `ai-review.yml` or
  `ai-contract-tests.yml` reads a routing value from `vars`. The Tier 2 contract-tests
  job today reads `vars.ZAI_BASE_URL` to route and to pick its credential, and
  `tests/unit/test_workflow_wiring.py` derives those expressions from `ai-review.yml`,
  so both workflows are rewired together and that test is re-pointed at the manifest.
- A separate secret-free job checks out `github.workflow_sha`, resolves the chain from
  the manifest there and publishes the chain and each lane's fields as job outputs. The
  review job consumes only those outputs, and its own trusted checkout, from which the
  wrapper, the classifier and the package are installed, moves from
  `pull_request.base.sha` to the same `github.workflow_sha`, so the code that enforces
  this ADR is never older than the manifest it is handed. Both are `main` commits, so
  the trusted-install property is unchanged. A structural test pins both checkout refs.
  With the YAML and the scripts taken from one commit, the two-PR bootstrap the workflow
  header requires is no longer needed for anything the workflow reads;
  [#2870](https://github.com/lgtm-hq/py-lintro/issues/2870) retires that header text.
  The credential-bearing Tier 2 job of `ai-contract-tests.yml` runs only on
  `workflow_dispatch` and reads the manifest from the dispatched ref. Its egress list is
  derived from the manifest's lane hosts by the same resolver, so the manifest stays the
  only place a host is defined; the structural test covers that job too.
- Egress for the review job is the union of the chain's host lists plus the static
  GitHub and toolchain hosts the job already allows. No lane may declare a wildcard
  host; the resolver fails closed on one.
- Each credential is forwarded by one literal `secrets.<NAME>` expression, gated on
  membership of the resolved chain. Secrets are never indexed dynamically.
- The wrapper refuses to run when the lane's credential is absent, when a competing
  credential is present in the environment or on disk, or when the provider's base-URL
  override names anything other than the lane endpoint. Each row declares that override
  variable (`base_url_env`): `ANTHROPIC_BASE_URL` for the Anthropic lanes,
  `OPENAI_BASE_URL` for the Codex lane, and `null` only for a CLI that reads no such
  variable (Cursor), in which case the check is that the lane endpoint is the CLI's
  built-in one. A row also declares any file-backed override its CLI reads (for Codex,
  the `chatgpt_base_url` key of `~/.codex/config.toml`), and the on-disk competing check
  covers those locations too; the exact keys are settled under
  [#2870](https://github.com/lgtm-hq/py-lintro/issues/2870). The wrapper itself exports
  `base_url_env` to the row's endpoint at the start of each attempt on a non-first-party
  lane, and leaves it unset on a first-party one; nothing else sets it. An unset
  override equals the provider's first-party endpoint, so on a first-party lane any set
  value is a refusal. An absent credential is a configuration error, red with
  `status=no-credential`, never a fallback trigger: the manifest promised a binding the
  secrets do not hold. The committed config and its `LINTRO_AI_*` overlays are not a
  second source either: the wrapper also refuses when the effective `ai.api_base_url` or
  `ai.api_key_env` is set and differs from the lane row, and when a configured
  `fallback_models` entry is outside the lane's allowed models. Provider, transport and
  model are exported from the row as the `LINTRO_AI_*` overlays, and the wrapper refuses
  when the effective `ai.provider`, `ai.transport` or `ai.model` still differs from the
  row.
- The initial manifest declares `claude-subscription` (the sole initial `chain` entry),
  `codex-subscription`, `cursor` and `zai-glm-gateway`. The last is
  `first_party: false`, binds `ZAI_AUTH_TOKEN`, egresses only to `api.z.ai:443`, and
  becomes active only by a merged manifest diff placing it first in `chain`.

The manifest and the resolver land first and inert, and the workflow rewire follows in a
second PR. That split is a staged rollout, not a bootstrap requirement: once the review
job checks out `github.workflow_sha`, a script and its first workflow invocation may
land together.

Delivered by: [#2869](https://github.com/lgtm-hq/py-lintro/issues/2869) (manifest and
resolver, inert), [#2870](https://github.com/lgtm-hq/py-lintro/issues/2870) (workflow
rewire, wrapper gate, docs), [#2873](https://github.com/lgtm-hq/py-lintro/issues/2873)
(a weekly smoke row per lane against its real endpoint).

### B. Provenance invariant

A run record reports where its requests went and what answered, not what was configured.

- `RunIdentity` gains `endpoint` (the host the transport was pointed at), `first_party`,
  `credential` (the kind actually forwarded: `claude_oauth`, `zai_token`, `codex_oauth`,
  `cursor_api_key`), `reported_models` (every model named by the CLI envelope's
  `modelUsage`, with its `canonicalModel` and `provider`), `lane`, and
  `model_verification` with exactly five states:
  - `verified`: on a first-party endpoint, every completed call reported model usage,
    the requested model is among the reported models once aliases are normalised to
    `canonicalModel`, and every reported model is in the lane's allowed models.
  - `mismatch`: on a first-party endpoint, a completed call reported model usage and the
    requested model is absent from it, or a reported model is outside the lane's allowed
    models.
  - `unverifiable`: the endpoint is not first-party, so the reported model cannot be
    trusted to describe what answered.
  - `unreported`: some completed call returned no model usage.
  - `legacy`: the round was stored before this ADR and carries none of these fields.
    Only the state reader assigns it; a run in the current workflow never produces it.

  The requested model of a call is the one the run asked for: the lane default, or a
  declared fallback or custom-agent override, each of which must be in the lane's
  allowed models. States are assigned in a fixed order so that no round qualifies for
  two: `legacy` by the state reader; then `unverifiable` whenever the endpoint is not
  first-party, whatever the envelope carried; then, over every completed call of the
  round, `mismatch` if any call is a mismatch, else `unreported` if any call carried no
  model usage, else `verified`. A non-first-party round that reports no model usage is
  therefore `unverifiable`, never `unreported`. A round that completed no call has no
  state of its own: the verdict rests on the reused rounds it inherits from, each judged
  on its stored state.

- `auth_mode` is no longer derived from the `--bare` policy; it follows `credential`.
- No renderer prints a configured model without the endpoint host beside it and the
  verification state. The sticky header, the badges, the cumulative history and the
  terminal display all follow this rule. When the state is not `verified`, a line at the
  top of the sticky body, outside any collapsible section, names the lane, the endpoint
  and the state.
- Every cost figure carries its basis. `CostBasis` (`lintro/ai/enums/cost_basis.py`)
  keeps `BILLED`, `ESTIMATED` and `UNPRICEABLE` and gains two values that split what
  `UNPRICEABLE` used to cover on the CLI path: `LIST_PRICE_SHADOW` (a first-party
  subscription run whose CLI envelope reports a list-price figure, shown for
  information) and `UNKNOWN` (a non-first-party endpoint, where the envelope's figure is
  priced on the wrong table and is discarded). `UNPRICEABLE` remains the value for a CLI
  run that reports no figure at all, and stored rounds carrying it render as they do
  today. An `UNKNOWN` basis renders as "n/a (third-party gateway)", never as a dollar
  figure and never as zero, and the cumulative history discloses that some rounds
  contributed no priced cost. Reporting and enforcement are separate: ADR-0007 rule 3
  enforces flag and environment caps on every cost basis, so a round on `UNKNOWN` or
  `LIST_PRICE_SHADOW` still settles `CostBudget` and the PR budget on a shadow figure.
  On `LIST_PRICE_SHADOW` that figure is the envelope's own list-price number, so the
  sticky and the cap use one value; on `UNKNOWN` it is the envelope's token counts
  priced on the lane's `shadow_price_model` list table. The figure is stored per PR as
  `cost_shadow_usd`, which never decreases, accumulates across rounds and fallback
  attempts for enforcement only, is labelled a shadow wherever cap progress is shown,
  and is never rendered as spend. On these two bases `usage.cost` is recorded as `0`, so
  `review_spend_usd` holds only billed and estimated money and `cost_shadow_usd` is the
  only place shadow cost accumulates; stored rounds from before this ADR keep their
  figure where it is. The PR budget's prior spend is
  `review_spend_usd + cost_shadow_usd`. A committed YAML cap, which ADR-0007 rule 3
  enforces on `billed` and `estimated` and treats as advisory on `unpriceable`, enforces
  on `UNKNOWN`, because a gateway bills real money, and is advisory on
  `LIST_PRICE_SHADOW`, as it is on `unpriceable` today. A non-first-party row declares
  `shadow_price_model`, the first-party model whose list table prices its shadow figure,
  and the resolver fails closed on a non-first-party row without one, or on any row
  whose default or allowed model has no list table and no `shadow_price_model`.
  `PrBudget.unpriceable` and the runtime-bound note it drives today key on `UNPRICEABLE`
  alone; the two new bases carry the shadow label the same way.
- Review-reuse validity includes the lane: a stored round from one lane is not reused as
  if it were produced by another. A stored round whose lane declares
  `reports_model_usage: true` but whose state is `unreported` is not reused either: its
  transport should have said what answered and did not, so it is re-reviewed rather than
  carried into a green check. A stored round in `mismatch` is not reused for the same
  reason: its coverage was produced by a model the lane does not allow, and a later
  unchanged round must not inherit it into a green check. The stored state is not taken
  on trust either: before coverage is inherited, every stored round's `lane` and
  `endpoint` are re-checked against the current manifest row, and on a row declared
  `first_party: true` its `reported_models` are re-checked against the row's allowed
  models as well, in the same order the states are assigned; a round that no longer
  passes is excluded as if it were `mismatch`, so tightening a first-party lane's
  allowed models re-reviews what those models covered. A non-first-party round's
  reported models are not re-checked, because the live rule does not trust them either.
  Historical cost figures are kept. A stored round with no lane field predates this ADR
  and records no endpoint, so nothing can say which lane produced it; from 2026-09-09
  until the interim measure in Consequences, such rounds were answered by GLM through
  Z.ai. It is therefore **not** reused for lane validity. It is kept in the cumulative
  history with `model_verification == legacy` and labelled "predates ADR-0011", and it
  never contributes to a live verdict. The first run after
  [#2870](https://github.com/lgtm-hq/py-lintro/issues/2870) therefore re-reviews each
  open PR once on the active lane; that one-time cost is accepted in preference to a
  green check built on rounds of unknown provenance.

Delivered by: [#2867](https://github.com/lgtm-hq/py-lintro/issues/2867) (run record
fields and `CostBasis` values),
[#2868](https://github.com/lgtm-hq/py-lintro/issues/2868) (sticky, badges, history and
display rendering).

### C. Verdict rules

`scripts/ci/classify_review_outcome.py` reads the new fields and applies:

| Condition                                                                                                                                                        | Verdict                                                                                                                                                                                                                                                                         |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Recorded `lane` is not in the resolved chain, or the recorded endpoint differs from the manifest endpoint of the recorded `lane`                                 | red, `status=endpoint-mismatch`                                                                                                                                                                                                                                                 |
| Recorded `lane` is in the chain but is not `chain[0]` (a fallback succeeded)                                                                                     | verdict unchanged; `::warning::` and the sticky fallback line of decision D                                                                                                                                                                                                     |
| Every lane of a chain with two or more entries failed before any chunk call completed                                                                            | red, `status=lanes-exhausted`                                                                                                                                                                                                                                                   |
| The only lane of a single-entry chain is unavailable, or any lane became unavailable after a chunk call completed (this row takes precedence over the one above) | red, `status=provider-unavailable`                                                                                                                                                                                                                                              |
| `model_verification == mismatch` on a first-party lane                                                                                                           | red, `status=model-mismatch`; a stored round in this state is not reused, so an unchanged rerun re-reviews rather than inherits it                                                                                                                                              |
| `model_verification == unverifiable` on a non-first-party lane                                                                                                   | verdict unchanged; `::warning::` naming lane and endpoint                                                                                                                                                                                                                       |
| `model_verification == unreported` on a first-party lane whose row declares `reports_model_usage: true`                                                          | red, `status=model-unreported`: the transport is declared to report what answered, so its absence is a transport defect. Applies to rounds produced by the current run; a stored round in this state is not reused, so it is re-reviewed rather than carried into a green check |
| `model_verification == unreported` on a first-party lane whose row declares `reports_model_usage: false`                                                         | verdict unchanged; the sticky prints the requested model as unverified by the transport                                                                                                                                                                                         |
| `model_verification == legacy`                                                                                                                                   | history rows only; such a round is never reused, so it never reaches the verdict                                                                                                                                                                                                |
| `model_verification == verified`                                                                                                                                 | verdict unchanged; no label                                                                                                                                                                                                                                                     |
| Authentication failure                                                                                                                                           | labelled by the recorded credential kind, not a fixed label                                                                                                                                                                                                                     |

A lane declared `first_party: false` is allowed to answer with an unverifiable model.
That is the price of opting into a gateway, and it is disclosed on the sticky rather
than hidden. It is never allowed to be reported as a first-party model.

Delivered by: [#2871](https://github.com/lgtm-hq/py-lintro/issues/2871).

### D. Degradation

The manifest's `chain` (decision A) is also the fallback order. The initial chain is
`["claude-subscription"]`; adding a second entry is a reviewed change to the manifest.

- The wrapper tries lanes in chain order. It advances only on the provider-unavailable
  class, which is exactly `ReviewErrorKind.AUTH_FAILED`, `INSUFFICIENT_CREDITS` and
  `QUOTA_EXCEEDED` from `lintro/ai/review/errors_taxonomy.py` and their
  `lintro.ai.liveness` equivalents, and only when no chunk call of the current attempt
  has completed. That set is a strict subset of `UNAVAILABLE_KINDS` in
  `lintro/ai/review/error_contract.py`; the chain walk keys on the error kind, never on
  the envelope's `provider_unavailable` flag, which is also true for `RATE_LIMITED`,
  `SERVER_ERROR`, `TIMEOUT` and `PROVIDER_UNAVAILABLE`. The first three are transient
  and stay with the existing retry (`RETRYABLE_KINDS`); `PROVIDER_UNAVAILABLE` means no
  provider is configured, a configuration failure that neither retries nor advances the
  chain. A review that has produced findings is never restarted on another lane.
- When the provider-unavailable class occurs after a chunk call has completed, the
  attempt stops, the partial round is persisted under ADR-0007 for a resume on the same
  lane, no other lane is tried in that run, and the check is red with
  `status=provider-unavailable`. A new run, started by a push or an `@lintro review`
  comment, walks the chain from `chain[0]` of the manifest at its own `workflow_sha`; if
  it falls back, the partial round from the dead lane is not reused on the new lane
  (decision B).
- The workflow forwards every credential in the chain, each by its own literal
  expression gated on chain membership, and egress covers the union of the chain's
  hosts. Because one job then holds every credential in the chain and can reach every
  host in it, every lane in a chain must carry the same `first_party` value as
  `chain[0]`: a non-first-party lane may be active only as a single-entry chain, and the
  resolver rejects a mixed chain and a chain whose first entry is non-first-party and
  that has more than one entry. The wrapper isolates each attempt: before the first
  attempt it captures every chain credential, including `CODEX_AUTH_JSON`, into
  non-exported shell state and unsets them, then for each attempt exports only the
  current lane's, so the environment the CLI transport forwards to its subprocess holds
  one credential. A file-backed credential is materialised from that state at the start
  of its own lane's attempt and deleted at its end, so the Codex session restore moves
  from a job step into the wrapper. The competing-credential check of decision A runs
  after that isolation, covers environment and file-backed credentials, and so holds per
  attempt rather than per job.
- A fallback is labelled: the sticky leads with the fallback line, the job summary
  carries a `::warning::`, and one deduplicated tracker issue labelled
  `ai-review-lane-down` is opened or updated for the dead lane through the minted App
  token.
- When the chain has two or more entries and every one of them failed before any chunk
  call completed, the check is red with `status=lanes-exhausted`. A single-entry chain
  whose lane is unavailable keeps the existing `status=provider-unavailable`, and so
  does any run in which a lane failed after a chunk call completed, whatever happened to
  earlier lanes: the mid-review case takes precedence. The decision C table carries the
  same split.
- The chain covers the provider-unavailable class only: dead or expired credential,
  exhausted balance, or exhausted quota. An absent credential, a rate limit, a provider
  that answers with errors mid-review, or an endpoint that is reachable but slow, is not
  a fallback trigger and is handled by the refusal in decision A or the existing retry,
  timeout and degradation rules.

Delivered by: [#2872](https://github.com/lgtm-hq/py-lintro/issues/2872).

### E. The "red is free" premise is withdrawn

The header of `ai-review.yml` and the preamble of `scripts/ci/run-ai-review.sh` are
corrected to state that the check, while not a required status, blocks merges under the
owner's merge rule, and that a provider or billing outage is therefore an incident with
a declared fallback (decision D), not a loud-but-harmless condition. The "repository
variable" and "egress to api.anthropic.com only" wording in the header, in
`docs/ai-review-transports.md` and in `docs/ai-features.md` is replaced by the lane
description.

Delivered by: [#2870](https://github.com/lgtm-hq/py-lintro/issues/2870).

### F. Relation to earlier ADRs

- [ADR-0010](0010-review-shape-and-protocol.md) **Robustness** row ("a provider outage,
  a 429 storm and a token expiry each produce a documented, user-visible outcome") is
  delivered for credential, credit and quota exhaustion by decisions B to D here. Its
  **Reliability** row's "a green check means a complete review" now also means a review
  whose endpoint matches its lane and whose model is one of: verified on a first-party
  lane whose transport reports model usage, labelled unverified-by-transport on a
  first-party lane declared `reports_model_usage: false`, or labelled unverifiable on a
  declared non-first-party one. A `legacy` round can never be part of such a check
  because it is never reused.
- [ADR-0008](0008-ai-review-architecture-invariants.md) invariant 2 (every provider call
  goes through `call_ai`) is the seam the fallback loop in decision D sits above: the
  chain is walked by the wrapper, per attempt, not by a second dispatch path inside the
  review.
- [ADR-0007](0007-review-resume-and-artifact-state.md): a stored review state produced
  without the decision B fields is read as `model_verification == legacy` and excluded
  from reuse, as decision B states. The resume model is unchanged for rounds that carry
  the fields and a verifiable or declared-unverifiable state; the three exclusions in
  decision B's reuse bullet (pre-ADR rounds, `unreported` on a lane that reports model
  usage, and `mismatch`) are re-reviewed rather than resumed. Its rule 3 is extended,
  not changed: `cap_is_enforced` learns the two new bases, with a committed YAML cap
  enforcing on `UNKNOWN` and advisory on `LIST_PRICE_SHADOW`, and flag and environment
  caps enforce on both as they do on every basis.
- [ADR-0009](0009-ai-provider-plugin-contract.md): reading `modelUsage` is a transport
  concern of the Anthropic CLI adapter, not a new provider-contract field. Providers
  whose transport reports no model usage record `unreported`.

## Consequences

The next lane switch is a reviewed diff to a committed JSON file, visible in the
repository and printed on every sticky. No variable at any scope is read for routing, so
an inherited organisation variable cannot change where a review goes. The price of that
guarantee is that an emergency switch is a merged one-line PR rather than a settings
click, and that it reaches an already-red PR through a new run (an `@lintro review`
comment), not through a rerun of the failed one. Because reuse validity includes the
lane, every lane switch, not only the first run after this ADR lands, re-reviews each
open PR once on the new lane. A reader of a sticky sees the lane, the endpoint host,
whether the model claim is verified, and what the cost figure is based on; the sticky
cannot again say Opus while GLM answers.

The cost is one more committed artifact to keep in step with the secrets that exist and
a resolver job on every review. Adding a lane now requires a manifest row, a smoke row
and a credential binding rather than a variable. The Z.ai lane is retained but honest:
it reports an unverifiable model and no dollar figure, and selecting it is an explicit
manifest change. A non-first-party lane cannot sit in a chain with a first-party one
(decision D), so a fallback review is never produced by an unverifiable model; opting
into the gateway means making it the sole lane, a deliberate owner choice when the chain
is edited.

Until [#2870](https://github.com/lgtm-hq/py-lintro/issues/2870) lands, the organisation
variable still exists and the gateway lane still activates on it. The interim measure
([#2865](https://github.com/lgtm-hq/py-lintro/issues/2865)) is to restrict the
variable's repository visibility so py-lintro no longer inherits it. The variable is
deleted once the manifest owns the endpoint.

Reopening decisions A to D is a new ADR that supersedes this one. The delivery columns
and the initial manifest contents are the edits this ADR invites.

## References

- [#2866](https://github.com/lgtm-hq/py-lintro/issues/2866) — this ADR
- [#2874](https://github.com/lgtm-hq/py-lintro/issues/2874) — epic: explicit review
  lanes, truthful provenance and declared degradation (sub-epic of
  [#2555](https://github.com/lgtm-hq/py-lintro/issues/2555))
- [#2865](https://github.com/lgtm-hq/py-lintro/issues/2865) — interim unblock: rescope
  the organisation variable
- [#2867](https://github.com/lgtm-hq/py-lintro/issues/2867),
  [#2868](https://github.com/lgtm-hq/py-lintro/issues/2868),
  [#2869](https://github.com/lgtm-hq/py-lintro/issues/2869),
  [#2870](https://github.com/lgtm-hq/py-lintro/issues/2870),
  [#2871](https://github.com/lgtm-hq/py-lintro/issues/2871),
  [#2872](https://github.com/lgtm-hq/py-lintro/issues/2872),
  [#2873](https://github.com/lgtm-hq/py-lintro/issues/2873) — delivery
- [#2473](https://github.com/lgtm-hq/py-lintro/pull/2473) — the gateway lane whose
  activation this ADR replaces;
  [#2472](https://github.com/lgtm-hq/py-lintro/issues/2472) its issue
- [#1826](https://github.com/lgtm-hq/py-lintro/issues/1826) — no-silent-skip, the origin
  of the "not required, so red is free" wording
- [#1923](https://github.com/lgtm-hq/py-lintro/issues/1923) — cost provenance, the
  origin of `CostBasis`
- `.github/workflows/ai-review.yml`, `scripts/ci/run-ai-review.sh`,
  `scripts/ci/classify_review_outcome.py`, `scripts/ci/ai_provider_smoke/providers.json`
- `lintro/ai/review/models/run_identity.py`, `lintro/ai/transport.py`,
  `lintro/ai/providers/anthropic/provider.py`, `lintro/ai/resolved_ai_config.py`,
  `lintro/ai/review/github_badges.py`, `lintro/ai/review/github_notes.py`
- [ADR-0007](0007-review-resume-and-artifact-state.md),
  [ADR-0008](0008-ai-review-architecture-invariants.md),
  [ADR-0009](0009-ai-provider-plugin-contract.md),
  [ADR-0010](0010-review-shape-and-protocol.md)
- [`docs/ai-review-transports.md`](../ai-review-transports.md),
  [`docs/ai-features.md`](../ai-features.md)
