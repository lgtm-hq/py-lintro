# ADR-0011: Dogfood review lanes, endpoint provenance and degradation

## Status

Accepted

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
  is not read (fixture `tests/fixtures/ai/cli_replay/claude/2.1.286.jsonl`).
- Cost was the CLI's `total_cost_usd`, priced on Anthropic's list price for a request
  Anthropic never served.
- The weekly provider smoke's `zai-api` row exercises Z.ai's OpenAI-protocol endpoint
  (`scripts/ci/ai_provider_smoke/providers.json`), not the `/api/anthropic` route the
  dogfood used, so the smoke could not detect a dead gateway.
- `RunIdentity` (`lintro/ai/review/models/run_identity.py`) has no endpoint field, so a
  run record cannot say where its requests went.

The incident surfaced on 2026-10-12 when the Z.ai balance ran out and every review went
red with `insufficient_credits`, holding eleven Renovate pull requests. The workflow
header and `scripts/ci/run-ai-review.sh` both state that "the check is deliberately NOT
required, so a billing condition is loud without blocking a merge". The check is indeed
not a required status on `main`, but the owner merges under a no-failing-checks rule, so
in practice a red dogfood review does block. The premise that red is free is false and
has been false since the no-silent-skip change in
[#1826](https://github.com/lgtm-hq/py-lintro/issues/1826).

Two independent design reviews (Claude Opus 5.5 and GPT-6.1 Sol, 2026-10-13) converged
on the same shape. The owner decided the four remaining points the same day:

1. Unblock by rescoping the organisation variable's visibility, not by code.
2. A repository variable selects a declared lane; it never supplies an endpoint.
3. Degradation follows a declared, ordered fallback chain.
4. The Z.ai lane is kept as an explicit opt-in, not removed.

Epic [#2874](https://github.com/lgtm-hq/py-lintro/issues/2874) carries the work; this
ADR is its design record and the acceptance criterion for each sub-issue.

## Decision

### A. Lane invariant

A **lane** is a named row in the committed manifest `scripts/ci/ai_review_lanes.json`. A
row declares the provider, transport, endpoint, whether the endpoint is first-party for
that provider, the name of the credential it binds, the exact egress hosts it needs, the
default model, the allowed models, and the cost basis its figures carry.

- The manifest is the **only** place an endpoint, a credential binding or an egress host
  may be defined. No workflow expression derives any of them from the `vars` context.
- An organisation-inherited variable may not select, enable or shape a lane. The only
  routing input is the repository-scoped `LINTRO_DOGFOOD_LANE`, which names a declared
  row. Unset means the manifest default. Any other value fails closed, before a secret
  is exposed, with the allowed lane list in the error.
- A separate secret-free job resolves the lane from the manifest at the base commit and
  publishes its fields as job outputs. The review job consumes only those outputs.
- Egress for the review job is the manifest row's host list plus the static GitHub
  infrastructure hosts. No lane may declare a wildcard host.
- Each credential is forwarded by one literal `secrets.<NAME>` expression, gated on the
  resolved lane id. Secrets are never indexed dynamically.
- The wrapper refuses to run when the lane's credential is absent, when a competing
  credential is present, or when `ANTHROPIC_BASE_URL` differs from the lane endpoint.
- The initial manifest declares `claude-subscription` (default), `codex-subscription`,
  `cursor` and `zai-glm-gateway`. The last is `first_party: false`, binds
  `ZAI_AUTH_TOKEN`, egresses only to `api.z.ai:443`, and is selected only by setting
  `LINTRO_DOGFOOD_LANE=zai-glm-gateway` on this repository.

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
  `model_verification` with exactly four states:
  - `verified`: the reported model equals the requested model on a first-party endpoint.
  - `mismatch`: the reported model differs from the requested one on a first-party
    endpoint.
  - `unverifiable`: the endpoint is not first-party, so the reported model cannot be
    trusted to describe what answered.
  - `unreported`: the transport returned no model usage at all.
- `auth_mode` is no longer derived from the `--bare` policy; it follows `credential`.
- No renderer prints a configured model without the endpoint host beside it and the
  verification state. The sticky header, the badges, the cumulative history and the
  terminal display all follow this rule. When the state is not `verified`, a line at the
  top of the sticky body, outside any collapsible section, names the lane, the endpoint
  and the state.
- Every cost figure carries its basis. `CostBasis` gains `LIST_PRICE_SHADOW` (a
  first-party subscription run priced at list for information) and `UNKNOWN` (a
  third-party endpoint whose pricing the run cannot know). An `UNKNOWN` basis renders as
  "n/a (third-party gateway)", never as a dollar figure and never as zero, and the
  cumulative history discloses that some rounds contributed no priced cost.
- Review-reuse validity includes the lane: a stored round from one lane is not reused as
  if it were produced by another.

Delivered by: [#2867](https://github.com/lgtm-hq/py-lintro/issues/2867) (run record
fields and `CostBasis` values),
[#2868](https://github.com/lgtm-hq/py-lintro/issues/2868) (sticky, badges, history and
display rendering).

### C. Verdict rules

`scripts/ci/classify_review_outcome.py` reads the new fields and applies:

| Condition                                                      | Verdict                                                     |
| -------------------------------------------------------------- | ----------------------------------------------------------- |
| Recorded endpoint differs from the resolved lane's endpoint    | red, `status=endpoint-mismatch`                             |
| `model_verification == mismatch` on a first-party lane         | red, `status=model-mismatch`                                |
| `model_verification == unverifiable` on a non-first-party lane | verdict unchanged; `::warning::` naming lane and endpoint   |
| Authentication failure                                         | labelled by the recorded credential kind, not a fixed label |

A lane declared `first_party: false` is allowed to answer with an unverifiable model.
That is the price of opting into a gateway, and it is disclosed on the sticky rather
than hidden. It is never allowed to be reported as a first-party model.

Delivered by: [#2871](https://github.com/lgtm-hq/py-lintro/issues/2871).

### D. Degradation

The manifest declares an ordered `chain` of lane ids. The initial chain is
`["claude-subscription"]`; adding a second entry is a reviewed change to the manifest.

- The wrapper tries lanes in chain order. It advances only on a provider-unavailable
  class from the existing failure taxonomy and `lintro.ai.liveness` (`auth_failed`,
  `insufficient_credits`, quota or rate-limit exhaustion), and only when no chunk call
  of the current attempt has completed. A review that has produced findings is never
  restarted on another lane.
- Each attempt runs with only its own lane's credential in the environment. The workflow
  forwards the union of the chain's credentials, each still by its literal gated
  expression, and egress covers the union of the chain's hosts.
- A fallback is labelled: the sticky leads with the fallback line, the job summary
  carries a `::warning::`, and one deduplicated tracker issue labelled
  `ai-review-lane-down` is opened or updated for the dead lane through the minted App
  token.
- When every lane in the chain is unavailable the check is red with
  `status=provider-unavailable`.

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
  delivered for endpoint and vendor outages by decisions B to D here. Its
  **Reliability** row's "a green check means a complete review" now also means a review
  whose endpoint and model match its lane.
- [ADR-0008](0008-ai-review-architecture-invariants.md) invariant 2 (every provider call
  goes through `call_ai`) is the seam the fallback loop in decision D sits above: the
  chain is walked by the wrapper, per attempt, not by a second dispatch path inside the
  review.
- [ADR-0007](0007-review-resume-and-artifact-state.md): a stored review state produced
  without the decision B fields is read as `model_verification == unreported` with an
  unknown lane, and is not reused across lanes.
- [ADR-0009](0009-ai-provider-plugin-contract.md): reading `modelUsage` is a transport
  concern of the Anthropic CLI adapter, not a new provider-contract field. Providers
  whose transport reports no model usage record `unreported`.

## Consequences

The next lane switch is a diff to a committed JSON file plus a repository variable, both
visible in the repository and both printed on every sticky. An inherited organisation
variable can no longer change where a review goes. A reader of a sticky sees the lane,
the endpoint host, whether the model claim is verified, and what the cost figure is
based on; the sticky cannot again say Opus while GLM answers.

The cost is one more committed artifact to keep in step with the secrets that exist, a
resolver job on every review, and a two-PR landing for any change the workflow must
read. Adding a lane now requires a manifest row, a smoke row and a credential binding
rather than a variable. The Z.ai lane is retained but honest: it reports an unverifiable
model and no dollar figure, and selecting it is an explicit repository setting. A chain
with a non-first-party lane in it means a fallback review may be produced by an
unverifiable model; that is permitted by decision C, labelled by decision D, and is a
deliberate owner choice when the chain is edited.

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
