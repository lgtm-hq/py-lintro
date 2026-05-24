# CI Scripts Directory

Scripts invoked by GitHub Actions workflows and local development helpers for CI tasks.

## Directory Structure

```bash
scripts/ci/
├── deployment/          # SBOM helpers and PyPI release validation
├── github/              # PR comment posting and cleanup
├── homebrew/            # Homebrew formula generation and tap PRs
├── maintenance/         # GHCR prune, security audit, egress checks
├── testing/             # Coverage badge, test summaries, image pull helpers
├── format-security-comment.py
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
| GHCR cleanup (scheduled)      | `maintenance/ghcr_prune_untagged.py` (shim → `ghcr_prune/cli.py`)           |

Release versioning and auto-tagging use lgtm-ci reusable workflows
(`release-version-pr.yml`, `release-auto-tag.yml`).

## GHCR Cache Tags

BuildKit registry cache is stored on production packages as `:cache` (not separate
`*-buildcache` repos). The prune utility reaps ephemeral `pr-*` / `mq-*` / `dispatch-*`
cache exports from `py-lintro` and `py-lintro-base` while preserving the permanent
`:cache` tag.

## Local Development

Many scripts support `--help`. Check individual headers for usage. Dogfooding scripts
expect a built `py-lintro:latest` image locally or in CI.
