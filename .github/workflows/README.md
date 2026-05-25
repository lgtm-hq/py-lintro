# Workflows overview

This repository uses GitHub Actions for quality gates, release automation, and
publishing. Shared workflows are thin callers to
[lgtm-ci](https://github.com/lgtm-hq/lgtm-ci) reusable workflows pinned at
`e42d374f1f89e0ad20d165cd611eb7732462b581` (**v0.18.2**; includes
[lgtm-ci#206](https://github.com/lgtm-hq/lgtm-ci/pull/206) host-user docker quality and
`lintro-tool-options` passthrough,
[lgtm-ci#211](https://github.com/lgtm-hq/lgtm-ci/pull/211) per-version coverage artifact
merge).

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
