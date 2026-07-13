# CI Scripts Directory

Scripts invoked by GitHub Actions workflows and local development helpers for CI tasks.

## Directory Structure

```bash
scripts/ci/
├── deployment/          # SBOM helpers and PyPI release validation
├── github/              # PR comment posting and cleanup
├── homebrew/            # Homebrew formula generation and tap PRs
├── maintenance/         # GHCR prune, security audit, egress checks
├── testing/             # Test summaries, image pull helpers
├── coverage-badge-update.sh  # Wrapper → testing/coverage-badge-update.sh
├── assert-required-check.sh
├── classify-osv-results.py
├── evaluate-code-quality-gate.sh
├── format-security-comment.py
├── is-infra-flake-failure.sh
├── run-code-quality-gate.sh
├── security-comment.sh
└── …                    # Tag/version helpers, manifest sync, etc.
```

## Workflow Mapping

| Workflow                      | Scripts                                                                     |
| ----------------------------- | --------------------------------------------------------------------------- |
| `test-ci.yml`                 | lgtm-ci reusable (coverage + PR comments)                                   |
| `docker-ci.yml`               | Fork detect, image pull/load, lgtm-ci quality, test summary, security audit |
| `publish-pypi-on-tag.yml`     | lgtm-ci quality/SBOM; `build-artifacts` + PyPI publish + GitHub release     |
| `pr-comment-cleanup.yml`      | `post-pr-delete-previous.sh`                                                |
| `lintro-report-scheduled.yml` | `lintro-report-generate.sh`                                                 |
| GHCR cleanup (docker-ci)      | `maintenance/delete-ci-ghcr-tags.sh`                                        |
| GHCR cleanup (scheduled)      | lgtm-ci `reusable-ghcr-cleanup.yml` (`ghcr-cleanup.yml`)                    |
| Vuln suppression check        | lgtm-ci `reusable-vuln-suppression-check.yml`                               |

Release versioning and auto-tagging use lgtm-ci reusable workflows
(`release-version-pr.yml`, `release-auto-tag.yml`).

## GHCR Cache Tags

BuildKit registry cache is stored on production packages as `:cache` (not separate
`*-buildcache` repos). Scheduled cleanup uses lgtm-ci `reusable-ghcr-cleanup.yml`, which
reaps ephemeral `pr-*` / `mq-*` / `dispatch-*` cache exports from `py-lintro` and
`py-lintro-base` while preserving referenced digests and the permanent `:cache` tag.

## Code Quality Gate

`docker-ci.yml` rolls up dogfooding lint attempts through these helpers:

- `evaluate-code-quality-gate.sh` — pick the effective lint attempt (retry wins on
  success) and normalize upstream outputs.
- `run-code-quality-gate.sh` — orchestrate evaluation plus `assert-required-check.sh`
  for the required gate job.
- `is-infra-flake-failure.sh` — classify runner infra flakes (cancelled jobs, exit 143,
  artifact timeouts).
- `assert-required-check.sh` — enforce the required check contract for
  lintro-code-quality.

## Local Development

Many scripts support `--help`. Check individual headers for usage. Dogfooding scripts
expect a built `py-lintro:latest` image locally or in CI.
