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
  `reusable-test-python.yml`
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
  (`🔥 AI CLI Invocation Smoke (Tier 2)`) is schedule/`workflow_dispatch` only and must
  stay non-required (live credentials). Admin ruleset PUT (not PATCH; preserve
  `bypass_actors` from a live GET):

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

## Release

- **release-version-pr.yml** — Opens version bump PR via
  `reusable-release-version-pr.yml` (Python ecosystem, auto-merge, max minor)
- **release-auto-tag.yml** — Creates tags on release commits via
  `reusable-release-auto-tag.yml` (`create-release: false`; GitHub Release is created by
  publish workflow)
- **mirror-release.yml** — On `release: published`, bumps the `lintro` pin in the
  `lgtm-hq/lintro-pre-commit` mirror, merges the version-bump PR, and tags the mirror
  `vX.Y.Z` so pre-commit consumers install the matching wheel (scripts under
  `scripts/ci/mirror/`; see `docs/pre-commit.md`)

Both callers set a dynamic `run-name` (event + branch) so post-merge release failures
are traceable from the Actions list rather than the default commit subject. The mirror
workflow (`mirror-release.yml`) uses a fixed run name because it is triggered by release
events rather than branch pushes. Failure visibility itself lives upstream: the
reusables run a `report-release-failure` job that writes trigger context to the step
summary and opens/updates a deduplicated GitHub issue on `main` failures — hence the
`actions: read` + `issues: write` job permissions.

## Publish

- **publish-pypi-on-tag.yml** — Production tag publish: `reusable-sbom` →
  `reusable-build-python-dist` → caller `pypi-upload` job (`prepare-pypi-upload` →
  `pypa/gh-action-pypi-publish` → `attest-build-provenance`) →
  `reusable-github-release`, then Homebrew (`build-binary.yml`) and Docker
  (`docker-build-publish.yml`). Upload via `pypa/gh-action-pypi-publish` (OIDC trusted
  publishing) runs in this workflow file, not in lgtm-ci reusables. Lint runs on `main`
  via `docker-ci` only (no duplicate quality on tag).
- **publish-testpypi.yml** — TestPyPI: `reusable-build-python-dist` + caller upload job
  (same three-step pattern with `repository-url: https://test.pypi.org/legacy/`)
- **docker-build-publish.yml** — Multi-arch GHCR publish via `reusable-docker.yml`
  (full + base images, registry cache at `:cache`, no-cache on version tags)
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
  **test-built-package.yml**, **build-binary.yml**

## Token patterns

- **`secrets.GITHUB_TOKEN`** — CI, PR comments, artifacts
- **`secrets.RELEASE_APP_*`** — Release PR and auto-tag (GitHub App installation token
  via lgtm-ci release workflows)
- **`secrets.DIGEST_APP_ID` / `secrets.DIGEST_APP_PRIVATE_KEY`** — The dedicated
  `lgtm-digest-bump` GitHub App (Contents read/write only), minted immediately before
  the candidate digest commit with explicit `permission-contents: write`. It is
  installed only on `py-lintro`; do not substitute `RELEASE_APP_*`.
- **`secrets.MIRROR_REPO_TOKEN`** — Cross-repo write to `lgtm-hq/lintro-pre-commit`
  (fine-grained PAT or GitHub App token with contents + pull-requests write on that
  repo) used by `mirror-release.yml`

## Concurrency

Standard pattern: `<workflow>-${{ github.ref }}` with
`cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}` for CI workflows. The
slower `docker-ci.yml` also maps `main` to `queue: max` and other refs to
`queue: single`: up to 100 main runs can wait without displacement (GitHub does not
guarantee dispatch order), while a new PR push still supersedes the prior pending and
in-progress run.
