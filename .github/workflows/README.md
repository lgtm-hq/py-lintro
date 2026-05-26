# Workflows overview

This repository uses GitHub Actions for quality gates, release automation, and
publishing. Shared workflows are thin callers to
[lgtm-ci](https://github.com/lgtm-hq/lgtm-ci) reusable workflows pinned at
`dfd52172e5ee817cb1b1baf24895209c5e18d5ad` (**v0.19.0** release commit; includes
[lgtm-ci#221](https://github.com/lgtm-hq/lgtm-ci/pull/221) annotated SHA enforcement and
[lgtm-ci#218](https://github.com/lgtm-hq/lgtm-ci/pull/218) Pages publish checkout-order
fix). All workflow SHA pins include trailing `# vX.Y.Z` comments so Renovate can track
digest updates. Policy is enforced by
[lgtm-ci validate-action-pinning](https://github.com/lgtm-hq/lgtm-ci/pull/221) (via
`validate-action-pinning.yml`) and automated by the
[org Renovate preset](https://github.com/lgtm-hq/.github/pull/12)
(`extends: local>lgtm-hq/.github:renovate-config`).

## CI (main branch)

- **test-ci.yml** — Python unit/component tests (3.11 + 3.14) via
  `reusable-test-python.yml`
- **docker-ci.yml** — Manifest sync, multi-stage Docker build, dogfooding quality
  (`reusable-quality.yml` + CI-built image), integration tests, security audit, GHCR
  publish (main), CI tag cleanup

## Release

- **release-version-pr.yml** — Opens version bump PR via
  `reusable-release-version-pr.yml` (Python ecosystem, auto-merge, max minor)
- **release-auto-tag.yml** — Creates tags on release commits via
  `reusable-release-auto-tag.yml` (`create-release: false`; GitHub Release is created by
  publish workflow)

## Publish

- **publish-pypi-on-tag.yml** — PyPI publish via `reusable-publish-pypi.yml`; bespoke
  GitHub Release assets, Homebrew (`build-binary.yml`), and Docker
  (`docker-build-publish.yml`)
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
