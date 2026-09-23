# Workflows overview

This repository uses GitHub Actions for quality gates, release automation, and
publishing. Shared workflows are thin callers to
[lgtm-ci](https://github.com/lgtm-hq/lgtm-ci) reusable workflows pinned at a single
canonical commit — read the current value off any `uses:` line rather than from here,
since a copy in prose only ever drifts (#1771). All SHA pins include trailing `# vX.Y.Z`
comments so Renovate can track digest updates. Policy is enforced by
[lgtm-ci validate-action-pinning](https://github.com/lgtm-hq/lgtm-ci/pull/221) (via
`validate-action-pinning.yml`) and automated by the
[org Renovate preset](https://github.com/lgtm-hq/.github/pull/12)
(`extends: local>lgtm-hq/.github:renovate-config`).

## CI (main branch)

- **test-ci.yml** — Python unit/component tests (3.11 + 3.14) via
  `reusable-test-python.yml`, plus the bats suites under `tests/bats` via
  `reusable-test-shell.yml` (`test-shell`, no coverage); all three feed `test-gate` and
  the required `test-suite-coverage` check.
- **docker-ci.yml** — Multi-stage Docker build, dogfooding quality
  (`reusable-quality-lint.yml` + PR-only `reusable-publish-quality-summary.yml`,
  CI-built image), integration tests, security audit, GHCR publish (main). Ephemeral
  `ci-<run_id>` tags are retained for partial reruns (#1138) and reclaimed by the weekly
  GHCR sweep. PRs without global-lint-impact changes lint only their changed files
  (`dogfooding-lint-changed`, same image/tool set); merge queue, pushes, and
  global-impact PRs keep the full-repo run (#1361)
- **dogfood-nightly.yml** — Nightly full-repo dogfooding lint on `main`
  (`reusable-quality-lint.yml`, pinned release image) backstopping changed-files PR
  linting; failures open/ping a deduplicated issue via
  `reusable-main-failure-notifier.yml`
- **ai-contract-tests.yml** — Two-tier AI CLI contract suite (#1609 / #1119). Tier 1
  (`🧾 AI CLI Flag Surface (Tier 1)`) runs `--version`/`--help` on every `pull_request`
  / `push` / `merge_group` with no path filter, so the context always reports and is
  safe to require on `checks-py-lintro` (16132640). Tier 2
  (`🔥 AI CLI Invocation Smoke (Tier 2, manual only)`) is `workflow_dispatch` only
  (#2600 removed its cron) and must stay non-required (live credentials). Admin ruleset
  PUT (not PATCH; preserve `bypass_actors` from a live GET):

  ```bash
  # Context string must match the job name in ai-contract-tests.yml —
  # source of truth: _AI_CONTRACT_TIER1_CONTEXT in tests/unit/test_workflow_wiring.py
  gh api orgs/lgtm-hq/rulesets/16132640 \
    | jq --arg ctx '🧾 AI CLI Flag Surface (Tier 1)' '
        . as $r
        | {
            name: $r.name,
            target: $r.target,
            enforcement: $r.enforcement,
            bypass_actors: ($r.bypass_actors // []),
            conditions: $r.conditions,
            rules: [
              $r.rules[]
              | if .type == "required_status_checks"
                  and ([.parameters.required_status_checks[]
                        | select(.context == $ctx)] | length == 0) then
                  .parameters.required_status_checks += [{context: $ctx}]
                else . end
            ]
          }
      ' \
    | gh api -X PUT orgs/lgtm-hq/rulesets/16132640 --input -
  ```

  Resulting `required_status_checks` must be the previous twelve contexts plus
  `🧾 AI CLI Flag Surface (Tier 1)` — never Tier 2.

- **ai-provider-api-smoke.yml** — Weekly live API smoke, one job per funded provider
  (#2600). The matrix comes from `scripts/ci/ai_provider_smoke/providers.json`, so a new
  provider is a table row plus a repository secret, never a workflow edit. Each row
  sends one trivial prompt through lintro's own API provider code path and posts a
  commit status `ai-provider-smoke/<row>` on `main`'s HEAD — `pending` (not green) when
  the row's secret is unset. Failures open/ping the deduplicated
  `reusable-main-failure-notifier.yml` issue under the `ai-provider-smoke` label, and
  the provider's error text is commented onto it.

## Release

- **release-version-pr.yml** — Opens version bump PR via
  `reusable-release-version-pr.yml` (Python ecosystem, no auto-merge, max minor). The
  `publish-gate` job runs `scripts/ci/check-last-publish-green.py` first and skips the
  version PR only when the newest version-tag publish run concluded `startup_failure` —
  the workflow itself being broken; every other conclusion, and any run still queued or
  in flight, is green (#2516, #2550). Dispatch with `force: true` to override.
- **release-auto-tag.yml** — Creates tags on release commits via
  `reusable-release-auto-tag.yml` (`create-release: false`; GitHub Release is created by
  publish workflow)
- **release-recover.yml** — `workflow_dispatch` entry for a partially published release
  (lgtm-ci#966): calls `reusable-release-recover.yml` with the original publish run's
  id, which re-reads that run's `release-assets` and `npm-dist` artifacts, resumes only
  the missing channels (GitHub Release assets, npm package set; Homebrew has no lane
  here, Docker is probed only) and records the outcome on the release-failure issue. Dry
  run by default; always run it first. Dispatch from `main`. Registered as an npm
  trusted publisher for the four packages, same `npm` environment as the tag path.
  Runbook: lgtm-ci `docs/release-recovery.md`.
- **mirror-release.yml** — Reusable (`workflow_call`), invoked by
  `publish-pypi-on-tag.yml` after the GitHub Release job; bumps the `lintro` pin in the
  `lgtm-hq/lintro-pre-commit` mirror, merges the version-bump PR, and tags the mirror
  `vX.Y.Z` so pre-commit consumers install the matching wheel (scripts under
  `scripts/ci/mirror/`; see `docs/pre-commit.md`). It cannot use `release: published`:
  the release is created with `GITHUB_TOKEN`, whose actions GitHub does not raise
  workflow events for, so that trigger fired zero times across ~30 releases (#2599).
  `workflow_dispatch` with a `release_tag` stays for manual backfill. A `mirror-token`
  guard job gates the call, so while the mirror App credentials are unset the mirror
  bump is skipped with a `::warning::` and a step-summary line instead of failing the
  tag run (#2622).

Both callers set a dynamic `run-name` (event + branch) so post-merge release failures
are traceable from the Actions list rather than the default commit subject. The mirror
workflow (`mirror-release.yml`) uses a fixed run name because it is called from the tag
pipeline rather than triggered by a branch push. Failure visibility itself lives
upstream: the reusables run a `report-release-failure` job that writes trigger context
to the step summary and opens/updates a deduplicated GitHub issue on `main` failures —
hence the `actions: read` + `issues: write` job permissions. The tag pipeline has its
own report: `notify-failure` in `publish-pypi-on-tag.yml` (`if: !cancelled()`, needs
every publish job) calls `reusable-release-failure-notifier.yml` with `toJson(needs)` as
the per-channel table and files or updates one issue keyed
`release-failure:publish-pypi-on-tag:<tag>`; a green rerun closes it. An attempt within
the auto-rerun budget (3, the same `max-reruns` and infra signatures as
`auto-rerun-on-infra-failure.yml`, pinned by a wiring test) whose failed job matches an
infra signature stays quiet so the automatic rerun gets its chance (#2562).

**Checkpoint prereleases.** The build-dist preflight (`reusable-build-python-dist.yml`)
requires the tag to equal the `pyproject.toml` version and to be reachable from `main`,
so a bare `aN` tag on `main` or on a side branch cannot publish. To exercise the gated
pipeline without a release: open a version-bump PR to `main` titled
`ci(release): checkpoint prerelease X.Y.ZaN (#issue)` that changes only
`pyproject.toml`, `lintro/__init__.py` and `uv.lock` (`uv version X.Y.ZaN`, no
`CHANGELOG.md`); the `ci` type keeps `release-auto-tag.yml` and the version-PR bot from
reacting. After the squash merge, push a signed `vX.Y.ZaN` tag on the merge commit by
hand. The tag run then builds, gates, publishes to PyPI and creates a prerelease GitHub
Release, and skips Docker promote, Homebrew and npm; the next bot version PR bumps past
the checkpoint version as usual. An `rcN` checkpoint can additionally open the
validation channels (see "Validation-only switches" below and
`docs/release-validation.md`).

## Publish

- **publish-pypi-on-tag.yml** — Production tag publish, build-then-publish (#2562).
  Build stage, all off `classify-tag`: `reusable-sbom` → `reusable-build-python-dist`
  (attests `dist/*`, writes `SHA256SUMS`, 90-day artifact); `build-binaries.yml` (three
  binaries, attested, plus `lintro.1`); `docker-build` calls `docker-build-publish.yml`
  in staging mode (build, scan, attest, cosign; run-scoped `build-<run_id>` tags only,
  no version or `latest`). **`release-gate`** needs every build job: downloads every
  artifact, runs `gh attestation verify` on each dist file (signer
  `lgtm-hq/lgtm-ci/.github/workflows/reusable-build-python-dist.yml`) and binary (signer
  `lgtm-hq/py-lintro/.github/workflows/build-binaries.yml`), checks the dist
  `SHA256SUMS`, downloads the Sigstore bundles (`<asset>.intoto.jsonl`), assembles the
  `release-assets` artifact with a `SHA256SUMS` over everything and writes
  `release-manifest.json` (image digests plus per-file sha256/size/bundle; both 90
  days). Publish stage: `pypi-upload` (`environment: pypi`, `prepare-pypi-upload` with
  `require-attestation`, then `pypa/gh-action-pypi-publish` as the last step — the first
  irreversible write of the run) → `reusable-github-release` (attaches `release-assets`
  with `checksums` and `immutable-assets`) → `docker-promote` (verify and cosign the
  staging digests, then retag to `<version>`, `<major.minor>`, `<major>`, `latest` as
  the last steps), `homebrew-tap` (`publish-binaries.yml`: reads the arm64 sha256 from
  the manifest and pings the tap), `npm-publish` (no longer behind Homebrew) and the
  mirror lane; `notify-failure` runs last under `!cancelled()` and reports the
  per-channel result (see Release above). Prereleases run the build stage, the gate,
  PyPI and the GitHub Release (marked prerelease, derived from `classify-tag`) and skip
  the Docker promote, Homebrew, npm and the pre-commit mirror bump, unless an `rcN` tag
  runs with the validation channels open (below). Lint runs on `main` via `docker-ci`
  only (no duplicate quality on tag).
- **docker-build-publish.yml** — Multi-arch GHCR build via `reusable-docker.yml` (base +
  full + ai images, registry cache at `:cache`). Called in `staging` mode by the tag
  pipeline; the `backfill_version`/`backfill_ref` dispatch still publishes a historical
  version directly until the recovery workflow (lgtm-hq/lgtm-ci#966) lands.
- **docker-tools-candidate.yml** — On an in-repository `renovate/**` push that changes a
  tool-version manifest, builds a candidate `lintro-tools` image and commits its digest
  to both Dockerfile pin sites. The app-token push retriggers PR checks; its
  `lgtm-digest-bump[bot]` actor fails the candidate job gate, so the commit cannot start
  a second candidate build. Renovate normally preserves that digest commit as a branch
  modification; a rebase that discards it simply causes the actor-gated flow to build a
  fresh candidate.
- **docker-tools-publish.yml** — Validates tools-image pull requests and runs the weekly
  no-cache rebuild for CVE freshness. Maintainer `workflow_dispatch` can publish a tools
  image explicitly. Merged Renovate candidates are promoted by digest, without a
  rebuild, by `docker-tools-promote.yml`.
- **docker-tools-promote.yml** — Classifies main pushes: merged Renovate PRs find their
  candidate and retag its exact digest as `lintro-tools:latest`; ordinary main tools
  changes (including installer/build-script updates) use a canonical publish fallback.
  Consumer-only digest pins are skipped. A merged Renovate PR with a missing candidate
  fails closed rather than rebuilding.

## Security & maintenance

- **ghcr-cleanup.yml** — Scheduled GHCR cleanup via `reusable-ghcr-cleanup.yml`
  (`py-lintro`, `py-lintro-base`) plus age-based sweeps of ephemeral `ci-*`, `sha-*`,
  `renovate-*`, and tools candidate tags. The reusable candidate build emits the custom
  candidate tag plus `sha-*`/`renovate-*` companion tags; candidates are removed when
  their PR is closed without merge or they are at least 14 days old. Versions with any
  persistent tag (such as promoted `latest`) are retained because GHCR deletes a whole
  package version, not one tag.
- **Digest-lag diagnostics** — `verify-manifest-tools.py` reports the tool, expected
  version, and lagging image tag/digest with the actionable `digest-bump required`
  message. It deliberately does not invent a PR number: the verifier runs inside an
  image and has no reliable pull-request API context.
- **vuln-suppression-check.yml** — Weekly OSV suppression staleness via
  `reusable-vuln-suppression-check.yml`
- **dependency-vuln-gate.yml** — Pre-merge mirror of the release SBOM vulnerability gate
  (#1667): same lgtm-ci syft/grype actions, same pin, same `fail-on: high` as
  `publish-pypi-on-tag.yml`'s `sbom` job, so a dependency change that would break the
  tagged publish fails on the PR instead. The release gate is `syft scan dir:.` over the
  whole repo, so the scan steps are gated (inside the job) on **every** language
  manifest that graph is cataloged from — Python (`pyproject.toml` / `uv.lock` /
  `requirements*.txt`), JavaScript (`package.json` / lockfiles incl. `bun.lock`), Rust
  (`Cargo.toml` / `Cargo.lock`) and Go (`go.mod` / `go.sum`) at any depth — a pure
  allow-list, not just the Python lock, or the pre-merge gate would be looser than the
  release gate. Unfiltered trigger, so the `🔐 Dependency Vulnerability Gate` context
  always reports and is safe to require
- **lintro-report-scheduled.yml**, **pr-comment-cleanup.yml**,
  **test-built-package.yml**, **build-binaries.yml**, **publish-binaries.yml**

## Binary release stages

The binaries ship in two `workflow_call` stages, both called from
`publish-pypi-on-tag.yml` (#2562):

- **build-binaries.yml** (`build-binaries` job in the caller) — `Build macOS Binary` /
  `Build Linux Binary` compile with Nuitka, run `verify_built_binary.sh` and
  `smoke-test-binary.py`, finalize, then **attest the finalized binary** with
  `actions/attest-build-provenance` (hard fail, no `continue-on-error`) and upload it
  and its `sha256-*` checksum as run artifacts. `Generate Man Page` uploads `lintro.1`
  the same way. Every job here is `contents: read`; the build jobs add `actions: read`
  (the reuse check below), `id-token: write` and `attestations: write`. Nothing in this
  workflow touches the release. It runs for prerelease tags too, so a prerelease can
  prove the build stage, but publishes nothing for them.
- **publish-binaries.yml** (`homebrew-tap` job in the caller, stable tags only, after
  `github-release`) — `Notify Homebrew Tap` reads the arm64 sha256 from the
  `release-manifest` artifact the gate wrote, waits for PyPI and dispatches the formula
  update. The binaries and `lintro.1` are attached to the release by
  `reusable-github-release` from the gate's `release-assets` artifact (under the
  `immutable-assets` rerun guard), so nothing in this workflow holds `contents: write`
  any more.

Artifacts (`lintro-macos-arm64`, `lintro-linux-x64`, `lintro-linux-arm64`, `sha256-*`,
`lintro-man-page`) are retained for 90 days, the policy recovery window
(lgtm-hq/lgtm-ci#962), so a publish rerun weeks later still finds what the run built.

Neither workflow has a `workflow_dispatch` trigger. The former `build-binary.yml` repair
dispatch resolved the _latest published_ release and republished the dispatched ref's
binaries onto it, which is how #2484 overwrote four `v0.151.1` assets; it is gone with
the split. Recovering a broken release is lgtm-hq/lgtm-ci#966.

## Binary release reruns

Every stage is idempotent, so **Re-run failed jobs** on a tag run is the supported
recovery and npm backfill path (#2247). There is no separate dispatch path. Start from
the `release-failure:publish-pypi-on-tag:<tag>` issue the notifier filed: its channel
table says what published and what did not, which is what decides between a rerun and
the recovery workflow (lgtm-hq/lgtm-ci#966).

- A failed build job rebuilds; a build job that already succeeded keeps its artifacts
  from the earlier attempt, and `release-gate` downloads those. Before compiling, each
  binary build job checks whether the release already carries its platform asset with a
  SHA256 matching the `sha256-*` artifact this same run produced on an earlier attempt
  (`scripts/build/reuse_release_asset.sh`); on the first attempt no release exists yet,
  so that check degrades to a rebuild, while a rerun after the release was created
  reuses the published asset and skips `Build binary`, `Verify binary`,
  `Smoke-test tool registry`, `Finalize binary` and `Attest build provenance` (the
  same-run artifact was written only after those passed, and the earlier attempt's
  attestation covers the same bytes).
- `release-gate` re-verifies every artifact on every attempt and re-assembles
  `release-assets` and `release-manifest` (both kept 90 days). A rerun of only the
  publish stage reuses the gate's artifacts from the earlier attempt.
- `github-release` attaches `release-assets` with `immutable-assets`: an asset already
  on the release with the same digest is a no-op, a different or unverifiable digest
  fails the job with the recovery rule instead of overwriting. No job in the tag
  pipeline deletes or renames a published asset any more; the former upload-then-swap
  path and its `<asset>.new` recovery are gone with the upload jobs. This is the
  reusable's own guard, not GitHub's immutable releases: that is a repository setting
  (Settings → General → Releases → "Immutable releases") which, once the owner enables
  it, locks every published release's assets and tag at the API level; releases show
  `immutable: false` until then.
- `docker-promote` verifies and cosign-signs the staging digests before its retags,
  which are the job's last steps and pin to the digests the build stage exported; a
  rerun retags the same digests again (registry no-op).
- `npm-publish` runs under the trusted workflow identity it needs on a rerun, because it
  is still called from the tag pipeline.

## Validation-only switches

Two repository **variables** (never secrets, so their state is visible in the run) exist
for the release validation runbook (`docs/release-validation.md`, #2633) and for nothing
else. Both are read exactly once, by `classify-tag` in `publish-pypi-on-tag.yml`, and
passed to the other jobs as outputs; no other job in the tag pipeline or any workflow it
calls reads `vars.*`. Both default off, and a wiring test runs the classifier with them
scrubbed to prove every gate then behaves as it always did.

- **`vars.RELEASE_VALIDATION_CHANNELS`** — `true` opens the validation channels for a
  PEP 440 `X.Y.ZrcN` tag only (output `validation_channels`; an alpha, a beta or a
  stable tag ignores it): `npm-publish` runs under dist-tag `next` (the PEP 440 version
  is mapped to its SemVer form, `0.160.3-rc.1`, for `package.json`), `docker-promote`
  runs but retags `<version>` alone (no `latest`, `major.minor` or `major`; the metadata
  steps switch between the SemVer and pep440 tag types on the same output),
  `homebrew-tap` and the mirror lane stay skipped.
- **`vars.RELEASE_FAULT`** — `fail-build` or `fail-publish-npm` (output
  `release_fault`). `release-gate`'s `Inject fault (fail-build)` step and
  `publish-npm.yml`'s `Inject fault (fail-publish-npm)` stage step (the fault reaches
  the called workflow as the `release_fault` input) each run
  `scripts/ci/release-fault.sh <name>`, which exits 1 with an `::error` annotation only
  when the output equals its name. The output is empty for every tag but an `rcN`, so a
  leftover variable cannot touch a stable release. Unset injects nothing; a value
  outside the allowlist (empty, `fail-build`, `fail-publish-npm`; for the channel switch
  empty, `true`, `false`) fails `classify-tag` before anything is written.

Delete both variables once a validation round is recorded.

## Token patterns

- **`secrets.GITHUB_TOKEN`** — CI, PR comments, artifacts
- **`secrets.RELEASE_APP_*`** — Release PR and auto-tag (GitHub App installation token
  via lgtm-ci release workflows)
- **`secrets.DIGEST_APP_ID` / `secrets.DIGEST_APP_PRIVATE_KEY`** — The dedicated
  `lgtm-digest-bump` GitHub App (Contents read/write only), minted immediately before
  the candidate digest commit with explicit `permission-contents: write`. It is
  installed only on `py-lintro`; do not substitute `RELEASE_APP_*`.
- **`secrets.MIRROR_APP_ID` / `secrets.MIRROR_APP_PRIVATE_KEY`** — The dedicated
  `lgtm-mirror-bot` GitHub App (Contents R/W + Pull requests R/W), installed only on
  `lintro-pre-commit`. Its installation token mints the mirror bump commit via
  `createCommitOnBranch` (GitHub-signed, attributed to `lgtm-mirror-bot[bot]`, as the
  mirror's rulesets require) and merges the bump PR; used by `mirror-release.yml`
  (#2742). `MIRROR_REPO_TOKEN` (a plain PAT) is retired.

## Concurrency

Standard pattern: `<workflow>-${{ github.ref }}` with
`cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}` for CI workflows. The
slower `docker-ci.yml` also maps `main` to `queue: max` and other refs to
`queue: single`: up to 100 main runs can wait without displacement (GitHub does not
guarantee dispatch order), while a new PR push still supersedes the prior pending and
in-progress run.
