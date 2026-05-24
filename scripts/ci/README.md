# CI Scripts Directory

Scripts invoked by GitHub Actions workflows and local development helpers for CI tasks.

## Directory Structure

```bash
scripts/ci/
├── deployment/          # SBOM helpers and PyPI release validation
├── github/              # PR comment posting and cleanup
├── homebrew/            # Homebrew formula generation and tap PRs
├── maintenance/         # GHCR prune, security audit, egress checks
├── testing/             # Docker dogfooding, coverage badge, test summaries
├── ci-pr-comment.sh     # Lintro PR comment generator (docker-ci dogfooding)
├── fail-on-lint.sh      # Fail dogfooding job when lintro check fails
├── format-lintro-pr-comment.py
├── format-security-comment.py
├── security-comment.sh
└── …                    # Tag/version helpers, manifest sync, etc.
```

## Workflow Mapping

| Workflow                      | Scripts                                                                                                                                                                                                        |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `quality-ci.yml`              | lgtm-ci reusable (no local scripts)                                                                                                                                                                            |
| `test-ci.yml`                 | lgtm-ci reusable (coverage + PR comments)                                                                                                                                                                      |
| `docker-ci.yml`               | `detect-fork-pr.sh`, `pull-ci-docker-images.sh`, `load-ci-docker-images.sh`, `delete-ci-ghcr-tags.sh`, `ci-lintro.sh`, `fail-on-lint.sh`, `ci-pr-comment.sh`, `extract-test-summary.sh`, `security-comment.sh` |
| `publish-pypi-on-tag.yml`     | lgtm-ci reusable publish (tag preflight built-in)                                                                                                                                                              |
| `pr-comment-cleanup.yml`      | `post-pr-delete-previous.sh`                                                                                                                                                                                   |
| `lintro-report-scheduled.yml` | `lintro-report-generate.sh`                                                                                                                                                                                    |
| GHCR cleanup (docker-ci)      | `maintenance/ghcr_prune_untagged.py`                                                                                                                                                                           |

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
