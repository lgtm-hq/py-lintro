# CI Scripts Directory

Scripts invoked by GitHub Actions workflows and local development helpers for CI tasks.

## Directory Structure

```bash
scripts/ci/
├── deployment/          # SBOM helpers and PyPI release validation
├── github/              # PR comment posting and cleanup
├── homebrew/            # Homebrew formula generation and tap PRs
├── mirror/              # lintro-pre-commit pin bump + tag automation
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
| `docker-build-publish.yml`    | `validate-docker-backfill-inputs.sh`, `resolve-allowed-endpoints.sh` (shared harden-runner allowlist, #1821)                     |
| `pr-comment-cleanup.yml`      | `post-pr-delete-previous.sh`                                                                                                     |
| `mirror-release.yml`          | `mirror/resolve-version.sh`, `mirror/wait-for-pypi-wheel.sh`, `mirror/publish-mirror-release.sh`                                 |
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
  lint passed on a failed job).
- `assert-required-check.sh` — enforce the required check contract for
  lintro-code-quality.
- `summarize-code-quality-gate.sh` — write the job-summary line that explains an
  infra-flaked gate.

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
`dogfooding_lint_retry` job is the remedy for a runner that died before reporting.

Fail-closed contract (#2296): classification is not absorption. A classified infra
failure that produced **no lint verdict** — a cancelled or timed-out job, a SIGTERM
`exit 143`, a tool-execution timeout — no longer greens the required check. The gate
writes `passed=false`, `status=no-verdict`, `infra-flake=true` and exits 1, so
`lintro-code-quality` goes red and `auto-rerun-on-infra-failure.yml` reruns the failed
jobs (up to three attempts). The check turns green only when a rerun produces a real
lint verdict. `infra-flake` is kept so the rerun bot, the job summary
(`summarize-code-quality-gate.sh`) and dashboards can tell "lint failed" apart from
"lint did not run"; a red check with `status=no-verdict` is runner loss, not a
violation.

The one absorbed class left is the mirror image: lint reported `status=passed` /
`exit-code=0` and only a post-lint step of the surrounding job failed (e.g. the report
artifact upload). That is a real verdict, so the check stays green with
`infra-flake=true` — and `publish` still refuses to promote the image on that basis, an
unchanged condition.

Tool-execution timeouts (#1653, #2242, #2296): a per-tool timeout inside lintro also
reports `status=failed` / `exit-code=1`, so it needs its own positive evidence. The
reusable lint workflow classifies the authoritative run's own JSON report and publishes
`timeout-flake` / `timed-out-tools`; `evaluate-code-quality-gate.sh` carries those
through the same attempt selection as the verdict, `run-code-quality-gate.sh` scopes
them to `verdict-source=lint`, and `is-infra-flake-failure.sh` absorbs only an exact
`true`. The classifier fails closed — it needs at least one timed-out tool, zero
findings from every tool, and no non-timeout failure. Changed-scope runs publish no JSON
report, so they pass an empty flag and stay red: that asymmetry is a decision, not an
omission. Since #2296 a proven timeout is diagnosed but no longer absorbed either: it
produced no lint verdict, so the gate goes red with `status=no-verdict` and the rerun
decides.

## Local Development

Many scripts support `--help`. Check individual headers for usage. Dogfooding scripts
expect a built `py-lintro:latest` image locally or in CI.
