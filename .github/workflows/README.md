# Workflows overview

This repository uses GitHub Actions for quality gates, release automation, and
publishing.

## Token Usage Guidelines

Workflows use one of two token patterns:

### Standard Token (`secrets.GITHUB_TOKEN`)

Use for regular CI operations that don't require elevated permissions:

- Quality checks (linting, testing, code scanning)
- PR comments and status updates
- Artifact uploads
- Dependency review

### Release Token Fallback (`secrets.RELEASE_TOKEN || secrets.GITHUB_TOKEN`)

Use for operations requiring elevated permissions or bypassing rate limits:

- **Tag creation/pushing** (auto-tag-on-main.yml)
- **Release PR creation** (semantic-release.yml)
- **Internal reference bumping** (auto-bump-internal-refs.yml)
- **SBOM generation on main** (sbom-on-main.yml)

The fallback ensures workflows function with standard token when RELEASE_TOKEN is
unavailable, while benefiting from elevated permissions when available.

## Concurrency Group Naming

All workflows use concurrency groups to prevent duplicate runs. The standard pattern is:

```yaml
<context>-${{ github.ref }}
```

Examples:

- `tests-${{ github.ref }}` - Test workflows
- `lintro-ci-${{ github.ref }}` - Lintro CI analysis
- `docker-${{ github.ref }}` - Docker build/test
- `pages-deploy-${{ github.ref }}` - GitHub Pages deployment

For workflows that should be unique per commit (e.g., manual TestPyPI publish), use
`${{ github.sha }}` instead of `${{ github.ref }}`.

## Quick Reference by File Name

- ci-pipeline.yml: Gated CI pipeline — resolve-tools, docker-build, lint, test (with
  coverage), smoke-test, publish, and deploy-pages (coverage to GitHub Pages on main).
- ci-lintro-analysis.yml: Run Lintro on PRs/pushes; Docker build; PR comment.
- lintro-report-scheduled.yml: Scheduled/manual Lintro run that uploads a markdown
  report.
- docker-build-publish.yml: Build/test Docker image; publish to GHCR on main/release.
- lintro-renovate.yml: Renovate bot.
- reusable-quality.yml: Reusable quality gate (Lintro format/check) exporting the
  project version.
- reusable-build.yml: Reusable build (uv sync, build sdist/wheel, twine check), uploads
  `dist/`.
- publish-pypi-on-tag.yml: Publish to PyPI on tag push (OIDC). Verifies pushed tag
  matches `pyproject.toml` version.
- publish-pypi-on-release.yml: [Removed] superseded by tag-based publishing.
- publish-testpypi.yml: Manual dispatch to publish to TestPyPI (OIDC), reusing quality
  and build workflows.
- release-create.yml: [Removed] replaced by Release PR → tag flow.
- auto-tag-on-main.yml: On push to `main` affecting `pyproject.toml`, guarded by a
  release-commit check, if the version changed and the tag doesn't exist, create and
  push the tag.
- semantic-release.yml: Computes next version and opens a Release PR via uvx
  python-semantic-release (no direct push to main).
- semantic-pr-title.yml: Validates PR title follows Conventional Commits and
  comments/fails if not, preventing merges until corrected.
