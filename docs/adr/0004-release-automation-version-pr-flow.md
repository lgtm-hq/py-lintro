# ADR-0004: Automated release via a version-PR flow

## Status

Accepted

## Context

Lintro needs a repeatable, low-friction release process that derives version bumps from
commit history rather than manual tagging. Conventional Commit prefixes already drive
the intended bump semantics — `docs`, `chore`, `refactor`, `style`, `test`, `ci`, and
`build` are no-bump, while `feat` and `fix` bump the version (documented in
`docs/contributing.md`).

The release automation itself is delegated to shared, reusable workflows from
`lgtm-hq/lgtm-ci` rather than reimplemented per repository. Two workflows in
`.github/workflows/` implement the flow:

- `release-version-pr.yml` runs on pushes to `main`, calls the reusable
  `reusable-release-version-pr.yml`, and opens (and auto-merges) a version-bump PR based
  on accumulated commits.
- `release-auto-tag.yml` is the paired workflow that tags the release once the version
  PR lands.

The two are deliberately paired (matching egress presets, guarded by
`test_release_workflows_use_paired_egress_presets`) and pinned to a specific `lgtm-ci`
tooling ref by SHA.

## Decision

Releases are automated with a version-PR flow built on the reusable `lgtm-hq/lgtm-ci`
workflows: a push to `main` opens an auto-merging version-bump PR, and a paired auto-tag
workflow tags the release. Version bumps are derived from Conventional Commit prefixes,
capped at a `minor` bump.

## Consequences

- Releases require no manual version bookkeeping; the bump follows from commit types, so
  commit discipline directly determines release semantics.
- Release logic lives in shared `lgtm-ci` workflows, reducing per-repo maintenance but
  coupling releases to that tooling and its pinned ref.
- The paired workflows must stay in sync (egress presets, tooling ref); the parity test
  enforces this and must be kept green.

## References

- `.github/workflows/release-version-pr.yml`, `.github/workflows/release-auto-tag.yml`.
- `docs/contributing.md` — Conventional Commit prefixes and bump semantics.
- `test_release_workflows_use_paired_egress_presets` — parity guard.
