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
  reviewed one-line diff to the manifest, which takes effect on merge for every open
  pull request because the resolver reads the manifest at `github.workflow_sha`, the
  protected default-branch commit that supplied the workflow definition, and not at
  `pull_request.base.sha`, which is the base as of the PR's last sync and can lag
  `main`. An empty chain, an entry that does not name a declared row, a row that
  declares a wildcard host, or a chain that mixes `first_party` values (decision D)
  fails closed in the resolver before a secret is exposed, with the allowed lane list in
  the error.
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
  review job consumes only those outputs. A structural test pins the resolver's checkout
  ref.
- Egress for the review job is the manifest row's host list plus the static GitHub
  infrastructure hosts. No lane may declare a wildcard host; the resolver fails closed
  on one.
- Each credential is forwarded by one literal `secrets.<NAME>` expression, gated on the
  resolved lane id. Secrets are never indexed dynamically.
- The wrapper refuses to run when the lane's credential is absent, when a competing
  credential is present in the environment or on disk, or when the provider's base-URL
  override (`ANTHROPIC_BASE_URL` for the Anthropic lanes, the corresponding variable for
  the Codex and Cursor CLIs) names anything other than the lane endpoint. An unset
  override equals the provider's first-party endpoint, so on a first-party lane any set
  value is a refusal.
- The initial manifest declares `claude-subscription` (the sole initial `chain` entry),
  `codex-subscription`, `cursor` and `zai-glm-gateway`. The last is
  `first_party: false`, binds `ZAI_AUTH_TOKEN`, egresses only to `api.z.ai:443`, and
  becomes active only by a merged manifest diff placing it first in `chain`.

Because the manifest and the resolver land before the workflow reads them (the two-PR
bootstrap the workflow header already requires for anything evaluated under
`pull_request_target`), the first PR is inert and the second rewires.

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
  - `unreported`: the transport returned no model usage at all.
  - `legacy`: the round was stored before this ADR and carries none of these fields.
    Only the state reader assigns it; a run in the current workflow never produces it.

  The requested model of a call is the one the run asked for: the lane default, or a
  declared fallback or custom-agent override, each of which must be in the lane's
  allowed models. States are assigned in a fixed order so that no round qualifies for
  two: `legacy` by the state reader; then `unverifiable` whenever the endpoint is not
  first-party, whatever the envelope carried; then, over every completed call of the
  round, `mismatch` if any call is a mismatch, else `unreported` if any call carried no
  model usage, else `verified`. A non-first-party round that reports no model usage is
  therefore `unverifiable`, never `unreported`.

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
  `LIST_PRICE_SHADOW` still settles `CostBudget` and the PR budget, using the envelope's
  token counts priced on the requested model's first-party list table as a shadow
  figure. That figure is stored as `cost_shadow_usd`, accumulates across rounds and
  fallback attempts for enforcement only, is labelled a shadow wherever cap progress is
  shown, and is never rendered as spend.
- Review-reuse validity includes the lane: a stored round from one lane is not reused as
  if it were produced by another. A stored round whose lane declares
  `reports_model_usage: true` but whose state is `unreported` is not reused either: its
  transport should have said what answered and did not, so it is re-reviewed rather than
  carried into a green check. A stored round with no lane field predates this ADR and
  records no endpoint, so nothing can say which lane produced it; from 2026-09-09 until
  the interim measure in Consequences, such rounds were answered by GLM through Z.ai. It
  is therefore **not** reused for lane validity. It is kept in the cumulative history
  with `model_verification == legacy` and labelled "predates ADR-0011", and it never
  contributes to a live verdict. The first run after
  [#2870](https://github.com/lgtm-hq/py-lintro/issues/2870) therefore re-reviews each
  open PR once on the active lane; that one-time cost is accepted in preference to a
  green check built on rounds of unknown provenance.

Delivered by: [#2867](https://github.com/lgtm-hq/py-lintro/issues/2867) (run record
fields and `CostBasis` values),
[#2868](https://github.com/lgtm-hq/py-lintro/issues/2868) (sticky, badges, history and
display rendering).

### C. Verdict rules

`scripts/ci/classify_review_outcome.py` reads the new fields and applies:

| Condition                                                                                                                        | Verdict                                                                                                                                                                                                                                                                         |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Recorded `lane` is not in the resolved chain, or the recorded endpoint differs from the manifest endpoint of the recorded `lane` | red, `status=endpoint-mismatch`                                                                                                                                                                                                                                                 |
| Recorded `lane` is in the chain but is not `chain[0]` (a fallback succeeded)                                                     | verdict unchanged; `::warning::` and the sticky fallback line of decision D                                                                                                                                                                                                     |
| `model_verification == mismatch` on a first-party lane                                                                           | red, `status=model-mismatch`                                                                                                                                                                                                                                                    |
| `model_verification == unverifiable` on a non-first-party lane                                                                   | verdict unchanged; `::warning::` naming lane and endpoint                                                                                                                                                                                                                       |
| `model_verification == unreported` on a first-party lane whose row declares `reports_model_usage: true`                          | red, `status=model-unreported`: the transport is declared to report what answered, so its absence is a transport defect. Applies to rounds produced by the current run; a stored round in this state is not reused, so it is re-reviewed rather than carried into a green check |
| `model_verification == unreported` on a first-party lane whose row declares `reports_model_usage: false`                         | verdict unchanged; the sticky prints the requested model as unverified by the transport                                                                                                                                                                                         |
| `model_verification == legacy`                                                                                                   | history rows only; such a round is never reused, so it never reaches the verdict                                                                                                                                                                                                |
| `model_verification == verified`                                                                                                 | verdict unchanged; no label                                                                                                                                                                                                                                                     |
| Authentication failure                                                                                                           | labelled by the recorded credential kind, not a fixed label                                                                                                                                                                                                                     |

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
  has completed. `RATE_LIMITED` is transient and stays with the existing retry; it never
  advances the chain. A review that has produced findings is never restarted on another
  lane.
- When the provider-unavailable class occurs after a chunk call has completed, the
  attempt stops, the partial round is persisted under ADR-0007 for a resume on the same
  lane, no other lane is tried in that run, and the check is red with
  `status=provider-unavailable`. A rerun walks the chain from `chain[0]` again; if it
  falls back, the partial round from the dead lane is not reused on the new lane
  (decision B).
- The workflow forwards every credential in the chain, each by its own literal
  expression gated on chain membership, and egress covers the union of the chain's
  hosts. Because one job then holds every credential in the chain and can reach every
  host in it, every lane in a chain must carry the same `first_party` value as
  `chain[0]`: a non-first-party lane may be active only as a single-entry chain, and the
  resolver rejects a mixed chain. The wrapper isolates each attempt: it unsets every
  environment credential that is not the current lane's, and a file-backed credential is
  materialised at the start of its own lane's attempt and deleted at its end, so the
  Codex session restore moves from a job step into the wrapper. The competing-credential
  check of decision A runs after that isolation, covers environment and file-backed
  credentials, and so holds per attempt rather than per job.
- A fallback is labelled: the sticky leads with the fallback line, the job summary
  carries a `::warning::`, and one deduplicated tracker issue labelled
  `ai-review-lane-down` is opened or updated for the dead lane through the minted App
  token.
- When every lane in the chain is unavailable the check is red with
  `status=provider-unavailable`.
- The chain covers the provider-unavailable class only: dead or expired credential,
  exhausted balance, or exhausted quota. A rate limit, a provider that answers with
  errors mid-review, or an endpoint that is reachable but slow, is not a fallback
  trigger and is handled by the existing retry, timeout and degradation rules.

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
  the fields and a verifiable or declared-unverifiable state; the two exclusions in
  decision B's reuse bullet (pre-ADR rounds, and `unreported` on a lane that reports
  model usage) are re-reviewed rather than resumed.
- [ADR-0009](0009-ai-provider-plugin-contract.md): reading `modelUsage` is a transport
  concern of the Anthropic CLI adapter, not a new provider-contract field. Providers
  whose transport reports no model usage record `unreported`.

## Consequences

The next lane switch is a reviewed diff to a committed JSON file, visible in the
repository and printed on every sticky. No variable at any scope is read for routing, so
an inherited organisation variable cannot change where a review goes. The price of that
guarantee is that an emergency switch is a merged one-line PR rather than a settings
click. A reader of a sticky sees the lane, the endpoint host, whether the model claim is
verified, and what the cost figure is based on; the sticky cannot again say Opus while
GLM answers.

The cost is one more committed artifact to keep in step with the secrets that exist, a
resolver job on every review, and a two-PR landing for any change the workflow must
read. Adding a lane now requires a manifest row, a smoke row and a credential binding
rather than a variable. The Z.ai lane is retained but honest: it reports an unverifiable
model and no dollar figure, and selecting it is an explicit manifest change. A
non-first-party lane cannot sit in a chain with a first-party one (decision D), so a
fallback review is never produced by an unverifiable model; opting into the gateway
means making it the sole lane, a deliberate owner choice when the chain is edited.

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
