# Contributing to Lintro

Thank you for your interest in contributing to Lintro! This document provides guidelines
and information for contributors.

## Conventional Commits (required)

We use Conventional Commits (Angular style) to drive automated versioning and releases.

- Format: `type(scope): subject` (scope optional)
- Types: feat, fix, docs, refactor, perf, test, chore, ci, style, revert
- Use imperative mood (e.g., "add", not "added").

Examples:

```text
feat: add new configuration option

- support for custom tool paths
- update documentation
- add integration tests
```

PR titles must also follow Conventional Commits. A PR check enforces this and will
comment with guidance if invalid.

### PR titles and version bumps

Semantic versioning is determined from the PR title (we squash-merge so the PR title
becomes the merge commit):

- **minor**: `feat(...)`
- **patch**: `fix(...)`, `perf(...)`
- **major**: add `!` after type or include a `BREAKING CHANGE:` footer
- **no bump**: `docs`, `chore`, `refactor`, `style`, `test`, `ci`, `build` (unless
  marked breaking)

Examples:

```text
feat(cli): add --group-by option            # minor
fix(parser): handle empty config            # patch
perf(ruff): speed up large file handling    # patch
refactor(core)!: rewrite execution model    # major (breaking)
chore: update dependencies                  # no bump

# Footer form for breaking change
refactor(core): unify plugin interfaces

BREAKING CHANGE: plugins must implement run() and report()
```

Notes:

- Use imperative mood (e.g., "add", not "added").
- If work is ambiguous (e.g., a large refactor), explicitly signal with `!` or a
  `BREAKING CHANGE:` footer.
- The PR title validator (`.github/workflows/semantic-pr-title.yml`) enforces the format
  before merge.

## Developer Certificate of Origin (required)

All contributions must be signed off under the Developer Certificate of Origin (DCO).
Use `git commit -s` (or `--signoff`) so your commit message contains a line like:

```text
Signed-off-by: Your Name <your.email@example.com>
```

See `DCO.md` for details. Pull requests without DCO sign-offs will be asked to amend
commits before merge.

## Quick Start

1. Clone the repository:

   ```bash
   git clone https://github.com/lgtm-hq/py-lintro.git
   cd py-lintro
   ```

2. Install dependencies:

   ```bash
   uv sync --dev
   ```

3. Run tests:

   ```bash
   ./scripts/local/run-tests.sh
   ```

4. Run Lintro on the codebase:

   ```bash
   ./scripts/local/local-lintro.sh check --output-format grid
   ```

## More Information

Release automation:

- Merges to `main` run semantic-release to determine the next version from commits and
  tag the repo.
- Tag push publishes to PyPI (OIDC) and creates a GitHub Release with artifacts.

For detailed contribution guidelines, see the project documentation or contact a
maintainer.

---

## Tool Integration Guidelines

This section describes how to add a new external tool to Lintro while keeping a
consistent UX and maintainable implementation.

### Principles

- Minimal defaults: invoke the native CLI without forcing special formats.
- Predictable discovery: put file-discovery rules into the tool implementation (e.g.,
  Actionlint filters to `/.github/workflows/`).
- Wrapper, not replacement: rely on tool defaults and parse its standard output; only
  add switches when strictly necessary for parsing.

### Implementation Steps

<!-- markdownlint-disable MD029 -- sequential numbering restarts after nested content blocks -->

1. Core code

- Create a tool plugin in `lintro/tools/definitions/` (subclass `BaseToolPlugin`, use
  `@register_tool`).
- Implement `definition` property returning `ToolDefinition`.
- Implement `check()` (and `fix()` only if the tool supports auto-fixes).
- Add a parser module in `lintro/parsers/<tool>/` for the tool's default output.
- Use unified formatter in `lintro/formatters/unified` (no tool-specific formatter
  needed).

2. Tests

- Put minimal violation samples in `test_samples/`.
- Add unit tests for the parser.
- Add integration tests that:
  - Run the tool directly (CLI) on the sample
  - Run the tool via Lintro and compare parity (issue counts, status)
  - Use realistic paths (e.g., `.github/workflows/` for Actionlint)
- Allow local skip when a system binary is missing (CI/Docker installs tools).

3. Installer support

- Update `scripts/utils/install-tools.sh` to install the binary.
- Prefer upstream installers when available (uv/pip, npm, official script, Homebrew).
  For OS/arch tarballs, encapsulate download/extract in a helper.
- Verify checksums when possible.

### Tool Version Sources (Including Companion Packages)

- `lintro/tools/manifest.json` is the canonical source of external tool versions used by
  Docker/CI. Add new tool binaries here.
- npm-managed tool versions come from `package.json` via `_NPM_PACKAGE_TO_TOOL` in
  `lintro/_tool_versions.py`.
- If a tool requires companion npm packages (for example, a checker plugin that ships
  alongside the main tool), add those packages to `package.json` and to
  `_COMPANION_NPM_PACKAGES` in `lintro/_tool_versions.py`.
- Use `get_tool_version("<npm-package-name>")` in `scripts/utils/install-tools.sh` to
  resolve companion package versions.
- Do not add companion packages to the manifest unless they provide a standalone binary
  tool.

4. Docs checklist

- Update `README.md` Supported Tools table and CLI examples.
- Update `docs/getting-started.md` with a short usage example.
- Update `docs/configuration.md` with discovery notes and usage tips.

5. CI and Docker

- The Docker image installs all supported tools; integration tests should run without
  skipping inside Docker/CI.

### Pass-through Options (optional)

Expose native flags via `--tool-options tool:key=value` only after the core behavior is
stable. Keep defaults minimal to avoid surprising users and to maintain parity with
direct CLI behavior.

---

## Code Review Process

All contributions go through code review:

1. **Automated checks**: CI runs tests, linting, and security audits
2. **Manual review**: A maintainer reviews code quality and design
3. **Approval required**: At least one approval before merge
4. **Squash merge**: PRs are squash-merged with the PR title as commit message

### What reviewers look for

- Code follows the [style guide](style-guide.md)
- Tests cover new functionality
- Documentation is updated if needed
- No security vulnerabilities introduced
- Commit messages follow Conventional Commits

---

## Local Development Commands

> See [Quick Start](#quick-start) above for initial setup. This section covers
> additional development workflows.

### Run the test suite

```bash
# Run all tests
./scripts/local/run-tests.sh

# Run with coverage
uv run pytest --cov=lintro --cov-report=term-missing

# Run specific tests
uv run pytest tests/unit/tools/ -v
```

### Run linting locally

```bash
# Full lintro check on the codebase
./scripts/local/local-lintro.sh check --output-format grid

# Format code
./scripts/local/local-lintro.sh format

# Run specific tools
uv run ruff check .
uv run mypy lintro/
```

### Build and test locally

```bash
# Install in development mode
uv sync --dev

# Build package
uv run python -m build

# Test the built package
uv pip install dist/*.whl
lintro --version
```

---

## Additional Resources

- [Style Guide](style-guide.md) - Code style and conventions
- [Architecture](architecture/ARCHITECTURE.md) - System design and decisions
- [Troubleshooting](troubleshooting.md) - Common issues and solutions
