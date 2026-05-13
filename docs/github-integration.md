# GitHub Integration Guide

This guide explains how to set up Lintro with GitHub Actions for automated code quality
checks, coverage reporting, and CI/CD integration.

## Quick Setup

The repository includes pre-configured GitHub Actions workflows. To activate them:

1. **Enable GitHub Pages** in repository settings (for coverage badges)
2. **Push to main branch** to trigger workflows
3. **Add badges** to your README.md (optional)

## Available Workflows

### 1. Quality Check Workflow

**File:** `.github/workflows/ci-lintro-analysis.yml`

**Features:**

- 🔍 **Comprehensive analysis** across all file types
- 🛠️ **Auto-fixing** with `lintro format` where possible
- 📊 **Detailed reporting** in GitHub Actions summaries
- 🚀 **Multi-tool analysis:**
  - Python: Ruff + pydoclint
  - Python formatting (post-check): Black
  - YAML: Yamllint
  - JSON: Prettier
  - Docker: Hadolint

**Triggers:**

- Pull requests
- Pushes to main branch
- Manual workflow dispatch

### 2. Test Suite, Coverage & Pages Deployment

**File:** `.github/workflows/ci-pipeline.yml` (jobs: `test`, `deploy-pages`)

**Features:**

- 🧪 **Test coverage reporting** with badges
- 🌐 **GitHub Pages deployment** for coverage reports (main branch only)
- 🔄 **Auto-updating** on each push to main

> Tip: Ensure Pages is enabled (Settings → Pages → Source: GitHub Actions)

### 4. Lintro Report Workflow

**File:** `.github/workflows/lintro-report-scheduled.yml`

**Features:**

- 📊 **Comprehensive codebase analysis** with Lintro
- 📈 **Report generation** in multiple formats (Grid, Markdown)
- 📋 **GitHub Actions summary** with detailed results
- 📦 **Artifact upload** for report retention
- 🌐 **Optional GitHub Pages deployment** for report hosting

If you want to publish the weekly report to Pages, prefer using a dedicated
`deploy-pages` job gated on the report workflow, following the pattern in
`ci-pipeline.yml`.

### 5. Complete CI Pipeline

**File:** `.github/workflows/ci-lintro-analysis.yml`

**Features:**

- 🎯 **Quality-first approach** - Lintro runs before tests
- 📋 **Combined reporting** - Quality + testing results
- 🚀 **Showcase integration** - Demonstrates Lintro capabilities

### 6. Docker Image Publishing

**File:** `.github/workflows/docker-build-publish.yml`

**Features:**

- 🐳 **Automated Docker image building** and publishing to GHCR
- 🏷️ **Smart tagging** - Latest, main branch, and semantic versions
- 🔄 **Release integration** - Images published on releases
- 📦 **GHCR integration** — Full image at `ghcr.io/lgtm-hq/py-lintro`
- 📦 **GHCR base image** — Minimal image at `ghcr.io/lgtm-hq/py-lintro-base`

### 7. OpenSSF Allstar (Repository Security Enforcement)

Allstar is an OpenSSF GitHub App that enforces repository security policies org-wide or
per-repo. To enable at the repo level:

- Create `.allstar/` with:
  - `allstar.yaml` → enable opt-in at repo level
  - `branch_protection.yaml`, `binary_artifacts.yaml`, `outside.yaml`, `security.yaml`
    each with `optConfig: { optIn: true }` and `action: issue` as a safe default.

Install and configure via the Allstar app and docs:

- App install: `https://github.com/apps/allstar-app`
- Policies and schema: `https://github.com/ossf/allstar#policies`
- Manual install guide: `https://github.com/ossf/allstar/blob/main/manual-install.md`

Notes:

- Org-wide management prefers an org `.allstar` repository with opt-out strategy.
- Repo-level configs require org `disableRepoOverride` to be false to take effect.

**Usage in CI/CD:**

You can use the published Docker image in your own CI/CD pipelines:

```yaml
# GitHub Actions example
- name: Run Lintro with Docker
  run: |
    docker run --rm -v ${{ github.workspace }}:/code \
      ghcr.io/lgtm-hq/py-lintro:latest check --output-format grid

# GitLab CI example
lintro:
  image: ghcr.io/lgtm-hq/py-lintro:latest
  script:
    - lintro check --output-format grid
```

## Setting Up in Your Repository

### 1. Copy Workflow Files

Copy the workflow files from this repository to your project:

```bash
mkdir -p .github/workflows
cp .github/workflows/*.yml your-project/.github/workflows/
```

### 2. Customize for Your Project

Edit the workflow files to match your project structure:

```yaml
# .github/workflows/ci-lintro-analysis.yml
- name: Run Lintro Quality Check
  run: |
    # Adjust paths for your project
    uv run lintro check src/ tests/ --tools ruff,pydoclint --output-format grid
    uv run lintro check .github/ --tools yamllint --output-format grid
    uv run lintro check *.json --tools prettier --output-format grid
```

### 3. Configure Repository Settings (optional for Pages)

**Enable GitHub Pages:**

1. Go to repository **Settings** → **Pages**
2. Select **Source:** "GitHub Actions"
3. Your coverage badge will be available at:
   `https://lgtm-hq.github.io/py-lintro/badges/coverage.svg`

## Release Automation (Single Release Train)

The repository ships with fully automated semantic releases and PyPI publishing.

- **Automated Release PR** (`.github/workflows/semantic-release.yml`)
  - On push to `main`, computes the next version from Conventional Commits
  - Updates `pyproject.toml` and `lintro/__init__.py`
  - Opens a Release PR (no direct push to main) and enables auto-merge; once checks
    pass, it merges

- **Auto Tag on Main** (`.github/workflows/auto-tag-on-main.yml`)
  - After the Release PR is merged, a guard step ensures the last commit matches
    `chore(release):` pattern
  - Detects the new version in `pyproject.toml`, and creates/pushes the tag if it does
    not already exist

- **Publish to PyPI on Tag** (`.github/workflows/publish-pypi-on-tag.yml`)
  - On tag push (e.g., `1.2.3`), verifies tag equals `pyproject.toml` version
  - Uses Trusted Publishing (OIDC) to upload to PyPI
  - Also creates a GitHub Release and attaches built artifacts

> End-to-end: Conventional commits → Release PR (auto-merged) → Tag created → PyPI
> publish.

### Permissions Model (least privilege)

- Default each workflow to `permissions: { contents: read }`.
- Grant elevated permissions only where required:
  - Tag creation job: `contents: write`.
  - PyPI publish job: `id-token: write` (for OIDC) and `contents: write` only if
    creating a GH Release.
  - PR comment jobs: `pull-requests: write`.

### Why we do not allow `astral-sh/setup-uv`

Our Actions policy requires that all actions (including transitive actions used by
composites) are allowlisted and pinned to a full commit SHA. The `astral-sh/setup-uv`
action invokes `actions/setup-python@v5` internally, which is both not on our allowlist
and referenced by tag (not a 40-char SHA). This causes policy enforcement to block any
job that uses `setup-uv`.

To comply, we replaced it with an internal composite `setup-env` that:

- installs `uv` via `pip` (no nested actions),
- provisions the requested Python version via `uv python install`, and
- syncs dependencies, keeping our pipeline policy-compliant.

Deprecated/manual flows (e.g., direct Release creation workflows) are removed to avoid
parallel release paths.

### Labels & guards

- Release PRs are labeled `release-bump` to make them easy to target in policies.
- Tagging is guarded in `auto-tag-on-main.yml` by checking the last commit title starts
  with `chore(release):` to ensure tags are only created after Release PR merges.

### Security & Pinning

- Third-party actions are pinned to commit SHAs for reproducibility and supply-chain
  safety.
- Official GitHub actions can also be pinned; we’ve pinned most for consistency.
- `pypa/gh-action-pypi-publish` remains on `release/v1` by policy (Trusted Publishing
  updates). If desired, pinning to a SHA is possible.

## Example Workflows

### Basic Quality Check

```yaml
name: Code Quality

on:
  pull_request:
  push:
    branches: [main]

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.13'

      - name: Install UV
        run: pip install uv

      - name: Install dependencies
        run: uv sync

      - name: Run Lintro
        run: |
          # Run core tools, then post-checks (Black) per pyproject config
          uv run lintro check --output-format grid --output lintro-results.txt
          cat lintro-results.txt

      - name: Upload results
        uses: actions/upload-artifact@v3
        if: always()
        with:
          name: lintro-results
          path: lintro-results.txt
```

### Auto-fix Pull Request

```yaml
name: Auto-fix Code Issues

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  autofix:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.13'

      - name: Install UV and dependencies
        run: |
          pip install uv
          uv sync

      - name: Run Lintro auto-fix
        run: uv run lintro format --output-format grid

      - name: Check for changes
        id: verify-changed-files
        run: |
          if [ -n "$(git status --porcelain)" ]; then
            echo "changed=true" >> $GITHUB_OUTPUT
          else
            echo "changed=false" >> $GITHUB_OUTPUT
          fi

      - name: Commit changes
        if: steps.verify-changed-files.outputs.changed == 'true'
        run: |
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add .
          git commit -m "style: auto-fix code issues with Lintro"
          git push
```

### Quality Gate

```yaml
name: Quality Gate

on:
  pull_request:

jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.13'

      - name: Install UV and dependencies
        run: |
          pip install uv
          uv sync

      - name: Run quality checks
        run: |
          # Try to auto-fix first
          uv run lintro format --output-format grid

          # Then check for remaining issues
          uv run lintro check --output-format grid --output quality-report.txt

          # Fail if critical issues remain
          if grep -q "error" quality-report.txt; then
            echo "❌ Critical quality issues found"
            cat quality-report.txt
            exit 1
          else
            echo "✅ Quality gate passed"
          fi
```

## Badge Integration

### Coverage Badge

Add to your README.md:

```markdown
![Coverage](https://lgtm-hq.github.io/py-lintro/badges/coverage.svg)
```

### Quality Badge

```markdown
![Code Quality](https://github.com/lgtm-hq/py-lintro/workflows/Code%20Quality/badge.svg)
```

### Custom Lintro Badge

```markdown
![Lintro](https://img.shields.io/badge/code%20quality-lintro-blue)
```

### OpenSSF Scorecard Badge

Add to your README.md:

```markdown
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lgtm-hq/py-lintro/badge)](https://scorecard.dev/viewer/?uri=github.com/lgtm-hq/py-lintro)
```

Reference installation docs:
`https://github.com/ossf/scorecard?tab=readme-ov-file#installation`.

## Advanced Configuration

### Tool-Specific Workflows

```yaml
# Python-only quality check
- name: Python Quality
  run: uv run lintro check src/ tests/ --tools ruff,pydoclint --output-format grid

# Frontend-only quality check
- name: Frontend Quality
  run: uv run lintro check assets/ --tools prettier --output-format grid

# Infrastructure quality check
- name: Infrastructure Quality
  run: uv run lintro check Dockerfile* --tools hadolint --output-format grid
```

### Matrix Builds

```yaml
strategy:
  matrix:
    python-version: ['3.11', '3.12', '3.13']
    tool: ['ruff', 'pydoclint', 'oxfmt']
```

### Conditional Execution

```yaml
- name: Run Lintro on changed files
  run: |
    # Get changed files
    git diff --name-only HEAD^ HEAD > changed-files.txt

    # Run Lintro only on changed files
    if [ -s changed-files.txt ]; then
      uv run lintro check $(cat changed-files.txt) --output-format grid
    else
      echo "No files changed"
    fi
```

## Troubleshooting

### Common Issues

**1. Workflow not triggering:**

- Check workflow file syntax
- Ensure proper indentation (YAML)
- Verify trigger conditions

**2. Permission denied:**

```yaml
- uses: actions/checkout@v4
  with:
    token: ${{ secrets.GITHUB_TOKEN }}
```

**3. Dependencies not installed:**

```yaml
- name: Install dependencies
  run: |
    pip install uv
    uv sync --dev
```

**4. Tool not found:**

```yaml
- name: Install system dependencies
  run: |
    sudo apt-get update
    sudo apt-get install -y hadolint
```

### Debug Workflow

```yaml
- name: Debug Lintro
  run: |
    echo "=== Environment ==="
    python --version
    uv --version

    echo "=== Available tools ==="
    uv run lintro list-tools

    echo "=== File structure ==="
    find . -name "*.py" | head -10

    echo "=== Running Lintro ==="
    uv run lintro check --output-format grid || true
```

## Integration Benefits

Using Lintro in GitHub Actions provides:

1. **Early Issue Detection** - Catch problems before they reach production
2. **Consistent Quality** - Enforce coding standards across all contributors
3. **Automated Fixes** - Reduce manual work with auto-fixing
4. **Comprehensive Reporting** - Multi-tool analysis in one place
5. **Quality Gates** - Block problematic code from merging
6. **Coverage Tracking** - Monitor test coverage over time

## Best Practices

1. **Run Lintro early** in your CI pipeline (before tests)
2. **Use auto-fix first**, then check for remaining issues
3. **Separate workflows** for different file types when needed
4. **Cache dependencies** to speed up workflows
5. **Use artifacts** to preserve reports
6. **Set up quality gates** to maintain code standards
7. **Monitor coverage trends** over time

This integration transforms your repository into a high-quality, maintainable codebase
with automated quality assurance! 🚀
