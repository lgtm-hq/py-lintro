# Workflows overview

This repository uses GitHub Actions for quality gates, release automation, and
publishing. Shared workflows are thin callers to
[lgtm-ci](https://github.com/lgtm-hq/lgtm-ci) reusable workflows pinned at
`ee8484ca71db3a2c2c33da6128bbf2330fcd7c88` (**v0.59.2**). All workflow SHA pins include
trailing `# vX.Y.Z` comments so Renovate can track digest updates. Policy is enforced by
[lgtm-ci validate-action-pinning](https://github.com/lgtm-hq/lgtm-ci/pull/221) (via
`validate-action-pinning.yml`) and automated by the
[org Renovate preset](https://github.com/lgtm-hq/.github/pull/12)
(`extends: local>lgtm-hq/.github:renovate-config`).

## CI (main branch)

- **test-ci.yml** — Python unit/component tests (3.11 + 3.14) via
  `reusable-test-python.yml`
- **docker-ci.yml** — Manifest sync, multi-stage Docker build, dogfooding quality
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

## Release

- **release-version-pr.yml** — Opens version bump PR via
  `reusable-release-version-pr.yml` (Python ecosystem, auto-merge, max minor)
- **release-auto-tag.yml** — Creates tags on release commits via
  `reusable-release-auto-tag.yml` (`create-release: false`; GitHub Release is created by
  publish workflow)

Both callers set a dynamic `run-name` (event + branch) so post-merge release failures
are traceable from the Actions list rather than the default commit subject. Failure
visibility itself lives upstream: the reusables run a `report-release-failure` job that
writes trigger context to the step summary and opens/updates a deduplicated GitHub issue
on `main` failures — hence the `actions: read` + `issues: write` job permissions.

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
- **docker-tools-publish.yml** — Publishes the `lintro-tools` toolchain base image
  (`docker/tools.Dockerfile`) via `reusable-docker.yml` on tool-pin changes plus a
  weekly no-cache rebuild for CVE freshness; cosign-signed with SBOM + provenance
  attestations. A follow-up root `Dockerfile` change will consume it via a
  Renovate-managed digest-pinned `FROM`.

## Security & maintenance

- **ghcr-cleanup.yml** — Scheduled GHCR cleanup via `reusable-ghcr-cleanup.yml`
  (`py-lintro`, `py-lintro-base`) plus age-based sweep of ephemeral `ci-*` tags
  (`sweep-ci-ghcr-tags.sh`, #1138)
- **vuln-suppression-check.yml** — Weekly OSV suppression staleness via
  `reusable-vuln-suppression-check.yml`
- **renovate.yml** — Daily dependency updates (lgtm-ci `harden-runner` +
  `secure-checkout`)
- **lintro-report-scheduled.yml**, **pr-comment-cleanup.yml**,
  **test-built-package.yml**, **build-binary.yml**

## Token patterns

- **`secrets.GITHUB_TOKEN`** — CI, PR comments, artifacts
- **`secrets.RELEASE_APP_*`** — Release PR and auto-tag (GitHub App installation token
  via lgtm-ci release workflows)

## Concurrency

Standard pattern: `<workflow>-${{ github.ref }}` with
`cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}` for CI workflows.
