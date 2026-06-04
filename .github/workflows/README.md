# Workflows overview

This repository uses GitHub Actions for quality gates, release automation, and
publishing. Shared workflows are thin callers to
[lgtm-ci](https://github.com/lgtm-hq/lgtm-ci) reusable workflows pinned at
`78a5255d51dc2e1e061e4103a95f3ca0c646275d` (**v0.32.1**). All workflow SHA pins include
trailing `# vX.Y.Z` comments so Renovate can track digest updates. Policy is enforced by
[lgtm-ci validate-action-pinning](https://github.com/lgtm-hq/lgtm-ci/pull/221) (via
`validate-action-pinning.yml`) and automated by the
[org Renovate preset](https://github.com/lgtm-hq/.github/pull/12)
(`extends: local>lgtm-hq/.github:renovate-config`).

## CI (main branch)

- **test-ci.yml** — Python unit/component tests (3.11 + 3.14) via
  `reusable-test-python.yml`
- **docker-ci.yml** — Manifest sync, multi-stage Docker build, dogfooding quality
  (`reusable-quality-lint.yml` + PR-only `reusable-quality-pr-comment.yml`, CI-built
  image), integration tests, security audit, GHCR publish (main), CI tag cleanup

## Release

- **release-version-pr.yml** — Opens version bump PR via
  `reusable-release-version-pr.yml` (Python ecosystem, auto-merge, max minor)
- **release-auto-tag.yml** — Creates tags on release commits via
  `reusable-release-auto-tag.yml` (`create-release: false`; GitHub Release is created by
  publish workflow)

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

## Security & maintenance (bespoke)

- **vuln-suppression-check.yml**, **lintro-report-scheduled.yml**,
  **pr-comment-cleanup.yml**, **test-built-package.yml**, **build-binary.yml**

## Token patterns

- **`secrets.GITHUB_TOKEN`** — CI, PR comments, artifacts
- **`secrets.RELEASE_APP_*`** — Release PR and auto-tag (GitHub App installation token
  via lgtm-ci release workflows)

## Concurrency

Standard pattern: `<workflow>-${{ github.ref }}` with
`cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}` for CI workflows.
