# Getting Started with Lintro

This guide will help you get up and running with Lintro quickly. Whether you're a new
user or looking to integrate Lintro into your project, this guide covers everything you
need to know.

## What is Lintro?

Lintro is a unified CLI tool that brings together multiple code quality tools under a
single interface. Instead of learning and configuring dozens of different linting and
formatting tools, Lintro provides:

- **One command** to rule them all
- **Consistent interface** across all tools
- **Beautiful output** with grid formatting
- **Auto-fixing** capabilities where possible
- **Multi-language support** for modern development stacks

## Installation

### Standard Installation

```bash
# Development installation (package not yet published)
git clone https://github.com/lgtm-hq/py-lintro.git
cd py-lintro
pip install -e .
```

### Development Installation

If you want to contribute or use the latest features:

```bash
# Clone the repository
git clone https://github.com/lgtm-hq/py-lintro.git
cd py-lintro

# Install with UV (recommended)
uv sync

# Or with pip
pip install -e .
```

## Requirements

### Python Version

Lintro requires **Python 3.11+**. This is a strict requirement - Lintro will not run on
older Python versions.

### Tool Dependencies

Lintro bundles several Python tools as direct dependencies for consistent behavior.
These tools are automatically installed when you install Lintro and their minimum
versions are centrally managed in `pyproject.toml`:

**Bundled Python Tools:**

- `ruff` - Fast Python linter and formatter
- `black` - Python code formatter
- `bandit` - Python security linter
- `mypy` - Python static type checker (runs in strict mode with
  `--ignore-missing-imports` by default unless you override via `set_options()` or
  `--tool-options`)
- `yamllint` - YAML linter
- `pydoclint` - Python docstring linter

### Optional External Tools

Some tools require separate installation. Their minimum versions are also managed in
`pyproject.toml`:

- `prettier` - JavaScript/TypeScript formatter (install via npm)
- `hadolint` - Dockerfile linter (download from GitHub releases)
- `actionlint` - GitHub Actions linter (download from GitHub releases)
- `semgrep` - Security scanner and code analyzer (`pipx install semgrep`,
  `pip install semgrep`, or `brew install semgrep`)
- `gitleaks` - Secret detection in git repos (`brew install gitleaks` or GitHub
  releases)
- `shellcheck` - Shell script analyzer (`brew install shellcheck` or GitHub releases)
- `shfmt` - Shell script formatter (`brew install shfmt` or GitHub releases)
- `sqlfluff` - SQL linter and formatter (`pip install sqlfluff`)
- `taplo` - TOML linter and formatter (`brew install taplo` or GitHub releases)
- `cargo-audit` - Rust dependency vulnerability scanner (`cargo install cargo-audit`)
- `cargo-deny` - Rust dependency license/advisory checker (`cargo install cargo-deny`)
- `typescript` - TypeScript compiler for type checking (`brew install typescript`,
  `bun add -g typescript`, or `npm install -g typescript`)
- `astro` - Astro type checker for `.astro` files (install locally with
  `bun add -d astro @astrojs/check` or `npm install --save-dev astro @astrojs/check`,
  then invoke with `bunx astro check` or `npx astro check`)
- `svelte-check` - Svelte type checker for `.svelte` files (install locally with
  `bun add -D svelte-check` or `npm install -D svelte-check`)
- `vue-tsc` - Vue TypeScript type checker for `.vue` files (`bun add -D vue-tsc` or
  `npm install -D vue-tsc`)

### Checking Versions

You can verify all tool versions with:

```bash
lintro list-tools
```

This command will show the current installed version of each tool alongside the minimum
required version, helping you identify any version mismatches.

### Docker Installation

For containerized environments or if you prefer not to install dependencies locally:

```bash
# Clone and setup
git clone https://github.com/lgtm-hq/py-lintro.git
cd py-lintro
chmod +x scripts/**/*.sh

# Use Lintro via Docker
./scripts/docker/docker-lintro.sh check --output-format grid
```

## First Steps

### 1. Verify Installation

```bash
# Check if Lintro is installed
lintro --help

# List available tools
lintro list-tools
```

### 2. Basic Usage

Start with checking your code:

```bash
# Check current directory
lintro check

# Auto-fix issues where possible
lintro format

# Check again to see remaining issues
lintro check
```

### 3. Understanding the Output

Lintro provides clear, structured output:

```text
┌─────────────────────┬──────┬───────┬─────────────────────────────────────┐
│ File                │ Line │ Code  │ Message                             │
├─────────────────────┼──────┼───────┼─────────────────────────────────────┤
│ src/main.py         │   12 │ F401  │ 'os' imported but unused           │
│ src/utils.py        │   25 │ E302  │ expected 2 blank lines             │
│ tests/test_main.py  │    8 │ D100  │ Missing docstring in public module │
└─────────────────────┴──────┴───────┴─────────────────────────────────────┘
```

## Output System: Auto-Generated Reports

Every time you run a Lintro command (check or fmt), Lintro automatically generates all
output formats for you in a timestamped directory under `.lintro/` (e.g.,
`.lintro/run-20240722-153000/`).

**You do not need to specify output format or file options.**

Each run produces:

- `console.log`: The full console output you saw during the run
- `results.json`: Machine-readable results for scripting or CI
- `report.md`: Human-readable Markdown report (great for sharing or documentation)
- `report.html`: Web-viewable HTML report (open in your browser)
- `summary.csv`: Spreadsheet-friendly summary of all issues

This means you always have every format available for your workflow, CI, or reporting
needs.

## Supported Languages and Tools

### Python Projects

```bash
# Check Python files
lintro check src/ tests/ --tools ruff,pydoclint

# Format Python code
lintro format src/ --tools ruff

# Run Black as a post-check (configured via pyproject)
lintro check src/

# Override Black on the fly
lintro check src/ --tool-options "black:line_length=100,black:target_version=py313"
```

**Tools:**

- **Ruff** - Fast Python linter and formatter
- **Black** - Python formatter (runs as a post-check by default)
- **pydoclint** - Docstring validation

### JavaScript/TypeScript Projects

```bash
# Check JS/TS files with Oxlint
lintro check src/ --tools oxlint

# Format JS/TS files with Oxfmt
lintro format src/ --tools oxfmt

# Combined lint + format
lintro check src/ --tools oxlint,oxfmt
```

**Tools:**

- **Oxlint** - Fast Rust-based linter for JS/TS
- **Oxfmt** - Fast Rust-based formatter for JS/TS
- **Prettier** - Code formatter for JSON, CSS, HTML, YAML, Markdown

### TypeScript Type Checking

```bash
# Type check TypeScript files with tsc
lintro check src/ --tools tsc

# With strict mode enabled
lintro check src/ --tools tsc --tool-options "tsc:strict=True"

# Use specific tsconfig
lintro check src/ --tools tsc --tool-options "tsc:project=tsconfig.app.json"

# Auto-install dependencies if node_modules is missing
lintro check src/ --tools tsc --auto-install
```

**Tools:**

- **TypeScript Compiler (tsc)** - Static type checking for TypeScript files

**Note:** If you see errors about missing modules (TS2307, TS2688, TS7016), your
`node_modules` may not be installed. Use the `--auto-install` flag to automatically run
`bun install` or `npm install` before checking, or install dependencies manually first.

### Markdown Files

```bash
# Check Markdown files
lintro check docs/ --tools markdownlint
```

**Tools:**

- **Markdownlint-cli2** - Markdown style validation

### YAML Files

```bash
# Check YAML configuration files
lintro check .github/ config/ --tools yamllint
```

**Tools:**

- **Yamllint** - YAML syntax and style validation
- **Actionlint** - GitHub Actions workflow validation (files under `.github/workflows/`)

```bash
# Validate GitHub workflows
lintro check --tools actionlint
```

### Docker Files

```bash
# Check Dockerfiles
lintro check Dockerfile* --tools hadolint
```

**Tools:**

- **Hadolint** - Dockerfile best practices

### Rust Projects

```bash
# Check Rust files with Clippy
lintro check src/ --tools clippy

# Auto-fix Clippy issues where possible
lintro format src/ --tools clippy

# Combined check and fix
lintro check src/ --tools clippy
```

**Tools:**

- **Clippy** - Official Rust linter with hundreds of lint rules

### Mixed Projects

```bash
# Check everything at once
lintro check

# Or be more specific
lintro check src/ --tools ruff,prettier
```

## Common Workflows

### Daily Development

```bash
# Check your changes before committing
lintro check

# Auto-fix what can be fixed
lintro format

# Check again to see remaining issues
lintro check
```

### Project Setup

```bash
# Initial project scan
lintro check --output initial-scan.txt

# Fix auto-fixable issues
lintro format

# Generate final report
lintro check --output final-report.txt
```

### CI/CD Integration

```bash
# CI-friendly check (no grid formatting)
lintro check --output ci-results.txt

# Exit with error if issues found
lintro check || exit 1
```

## Configuration

### Using Tool Configuration Files

Lintro respects each tool's native configuration:

**Python (Ruff):**

```toml
# pyproject.toml
[tool.ruff]
line-length = 88
select = ["E", "F", "W", "I"]
```

**YAML (Yamllint):**

```yaml
# .yamllint
extends: default
rules:
  line-length:
    max: 120
```

**Prettier:**

```json
{
  "tabWidth": 2,
  "semi": true,
  "singleQuote": true
}
```

### Lintro-Specific Options

```bash
# Tool options use key=value (lists with |, booleans True/False)
lintro check --tool-options "ruff:line_length=88,prettier:print_width=80"

# Exclude patterns
lintro check --exclude "migrations,node_modules,dist"

# Include virtual environments (not recommended)
lintro check --include-venv

# Group output by error type
lintro check --output-format grid --group-by code
```

## Tips and Tricks

### 1. Use Grid Formatting

Always use `--output-format grid` for better readability:

```bash
lintro check
```

### 2. Group by Error Type

When fixing multiple similar issues:

```bash
lintro check --group-by code
```

### 3. Focus on Specific Tools

For faster checks in large codebases:

```bash
# Only check Python formatting
lintro check --tools ruff

# Only check documentation
lintro check --tools pydoclint
```

### 4. Save Results for Analysis

```bash
# Save detailed report
lintro check --output quality-report.txt

# Review offline
cat quality-report.txt
```

### 5. Incremental Fixing

Fix issues incrementally by tool type:

```bash
# Fix formatting issues first (auto-fixable)
lintro format --tools ruff,prettier

# Then address linting issues
lintro check --tools pydoclint,yamllint
```

## Integration Examples

### Pre-commit Hook

Create `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: lintro
        name: Lintro Quality Check
        entry: lintro check --output-format grid
        language: system
        pass_filenames: false
```

### Makefile Integration

<!-- markdownlint-disable MD010 -->

```makefile
.PHONY: lint fix check

lint:
	lintro check --output-format grid

fix:
	lintro format --output-format grid

check: lint
	@echo "Quality check completed"
```

<!-- markdownlint-enable MD010 -->

### VS Code Integration

Add to `.vscode/tasks.json`:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Lintro Check",
      "type": "shell",
      "command": "lintro",
      "args": ["check", "--output-format grid"],
      "group": "test",
      "presentation": {
        "echo": true,
        "reveal": "always",
        "focus": false,
        "panel": "shared"
      }
    }
  ]
}
```

## Troubleshooting

### Common Issues

**1. Tool not found:**

```bash
# Check which tools are available
lintro list-tools

# Install missing tools
pip install ruff pydoclint
```

**2. No files to check:**

```bash
# Check file patterns
lintro check --output-format grid .

# Include specific file types
lintro check --output-format grid "**/*.py"
```

**3. Too many issues:**

```bash
# Focus on specific tools
lintro check --tools ruff

# Exclude problematic directories
lintro check --exclude "legacy,migrations"
```

**4. Permission errors:**

```bash
# Check file permissions
ls -la

# Use sudo if needed (not recommended)
sudo lintro check
```

### Getting Help

- **Command help:** `lintro --help` or `lintro check --help`
- **List tools:** `lintro list-tools --show-conflicts`
- **GitHub Issues:** Report bugs or request features
- **Documentation:** Check other guides in the `docs/` directory

## AI-Powered Features

Lintro can optionally use AI to provide actionable summaries and interactive fix
suggestions.

```bash
# Install AI dependencies
uv pip install 'lintro[ai]'
export ANTHROPIC_API_KEY=sk-ant-...

# AI summary is automatic when enabled in config
lintro check

# Add interactive AI fix suggestions
lintro check --fix
```

See the [AI Features Guide](ai-features.md) for full setup and configuration.

## Next Steps

Now that you're familiar with the basics:

1. **Explore advanced features** - Check out the [Configuration Guide](configuration.md)
2. **Enable AI features** - See the [AI Features Guide](ai-features.md)
3. **Set up CI/CD** - See the [GitHub Integration Guide](github-integration.md)
4. **Use Docker** - Read the [Docker Usage Guide](docker.md)
5. **Contribute** - Check the [Contributing Guide](contributing.md)
6. **Analyze tools** - Dive into [Tool Analysis](tool-analysis/) for detailed
   comparisons

Welcome to the Lintro community! 🚀
