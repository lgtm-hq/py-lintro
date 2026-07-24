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

| Workflow                      | Scripts                                                                                                                          |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `test-ci.yml`                 | lgtm-ci reusable (coverage + PR comments)                                                                                        |
| `docker-ci.yml`               | Fork detect, image pull/load, lgtm-ci quality, test summary, security audit                                                      |
| `publish-pypi-on-tag.yml`     | lgtm-ci quality/SBOM; `build-artifacts` + PyPI publish + GitHub release                                                          |
| `pr-comment-cleanup.yml`      | `post-pr-delete-previous.sh`                                                                                                     |
| `lintro-report-scheduled.yml` | `resolve-lintro-image.sh`, `pull-lintro-image.sh`, `lintro-report-generate.sh`                                                   |
| GHCR cleanup (scheduled)      | lgtm-ci `reusable-ghcr-cleanup.yml` + `maintenance/sweep-ci-ghcr-tags.sh` (`ghcr-cleanup.yml`, #1138)                            |
| Vuln suppression check        | lgtm-ci `reusable-vuln-suppression-check.yml`; local `security/install-osv-scanner.sh` and `security/check-vuln-suppressions.sh` |

Release versioning and auto-tagging use lgtm-ci reusable workflows
(`release-version-pr.yml`, `release-auto-tag.yml`).

## GHCR Cache Tags

BuildKit registry cache is stored on production packages as `:cache` (not separate
`*-buildcache` repos). Scheduled cleanup uses lgtm-ci `reusable-ghcr-cleanup.yml`, which
reaps ephemeral `pr-*` / `mq-*` / `dispatch-*` cache exports from `py-lintro` and
`py-lintro-base` while preserving referenced digests and the permanent `:cache` tag.
Ephemeral run-scoped `ci-*` tags from docker-ci are retained for partial reruns and
reclaimed by `sweep-ci-ghcr-tags.sh` (age-based, default 91 days; #1138).

## Code Quality Gate

`docker-ci.yml` rolls up dogfooding lint attempts through these helpers:

- `evaluate-code-quality-gate.sh` — pick the effective lint attempt (prefer retry
  whenever it ran) and normalize upstream outputs.
- `run-code-quality-gate.sh` — orchestrate evaluation plus `assert-required-check.sh`
  for the required gate job.
- `is-infra-flake-failure.sh` — classify runner infra flakes (cancelled jobs, exit 143,
  artifact timeouts).
- `assert-required-check.sh` — enforce the required check contract for
  lintro-code-quality.

Safety contract (#1313): a failure is only absorbed when there is positive evidence that
lint itself did not report a violation — a cancelled/timed-out job that reported no
verdict, a SIGTERM `exit 143`, or lint outputs that say `status=passed` / `exit-code=0`.
A genuine lint failure always reports `status=failed` / `exit-code=1`, and that guard
sits above the cancellation branch, so even a run cancelled after lint failed stays red
— _except_ when the reported exit code is exactly `143`. That check deliberately sits
above the guard: `143` is `128 + SIGTERM`, assigned by the kernel when the runner kills
the process, and lintro itself only ever exits `0` or `1` for a lint verdict. A
SIGTERM'd run often writes a stale `status=failed` on its way out, so `143` overrides it
and absorbs. Missing outputs are _not_ evidence of a flake and stay red; the bounded
`dogfooding_lint_retry` job is the remedy for a runner that died before reporting. When
the gate does absorb noise it sets `infra-flake=true`, and `publish` refuses to promote
an image on that basis.

## Local Development

Many scripts support `--help`. Check individual headers for usage. Dogfooding scripts
expect a built `py-lintro:latest` image locally or in CI.
