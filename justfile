# Justfile for py-lintro development.
# Run `just --list` to see all available recipes.
#
# Install just (do not pipe a remote installer into bash):
#   macOS:  brew install just
#   Linux:  cargo install just

set dotenv-load
set shell := ["bash", "-euo", "pipefail", "-c"]

# Aliases kept for muscle memory from the previous Makefile targets.
alias lintro-check := lint
alias lintro-format := format
alias chk := lint
alias fmt := format

# Show all available recipes
default:
    @just --list

# Regenerate the derived version artifacts from their sources (#2180)
generate:
    python3 scripts/ci/generate-tool-versions.py
    python3 scripts/ci/generate-builtin-tool-index.py

# Set up development environment with uv
setup:
    @echo "Setting up development environment with uv..."
    uv sync --dev --extra full
    uv pip install -e .
    @echo "Setup complete! Try 'just test' or 'just lint'"

# Lint then run the test suite (local pre-commit stand-in)
pre-commit: lint test

# Install the package
install:
    @echo "Installing package with uv..."
    uv sync --dev --extra full
    uv pip install -e .

# Run all tests with coverage (runs mypy first)
test: mypy
    @echo "Running tests with coverage..."
    uv run lintro tst tests/ --tool-options pytest:coverage_report=True,pytest:coverage_html=htmlcov,pytest:coverage_xml=coverage.xml,pytest:timeout=600
    @echo "Coverage reports generated:"
    @echo "  - Terminal: displayed above"
    @echo "  - HTML: htmlcov/index.html"
    @echo "  - XML: coverage.xml"

# Run integration tests (the real runner, not the local-test.sh stub)
test-integration:
    @echo "Running integration tests..."
    ./scripts/local/run-tests.sh

# Run unit tests only (faster). Pass pytest flags after `--`
# so just does not consume them (e.g. `just test-unit -- -v`).
test-unit *ARGS:
    uv run pytest tests/unit {{ ARGS }}

# Run linting with lintro (runs mypy first)
lint: mypy
    @echo "Running lintro check..."
    uv run lintro check .

# Run linting with specific tools (spaces become commas for --tools)
# e.g. `just lint-tools mypy ruff` or `just lint-tools mypy,ruff`
lint-tools +TOOLS:
    uv run lintro check . --tools "{{ replace(TOOLS, " ", ",") }}"

# Format code with lintro
format:
    @echo "Running lintro format..."
    uv run lintro format .

# Run mypy type checking via lintro
mypy:
    @echo "Running mypy type checking via lintro..."
    uv run lintro check . --tools mypy

# Hyperfine CLI overhead benchmarks (lintro vs direct tools). See #598.
# Requires hyperfine on PATH: brew/cargo install hyperfine
bench:
    @echo "Running hyperfine CLI overhead benchmarks..."
    ./benchmarks/run-hyperfine.sh

# Build Docker image (full target)
docker-build:
    @echo "Building Docker image..."
    docker build --target full -t py-lintro:latest .

# Run tests in Docker
docker-test:
    @echo "Running tests in Docker..."
    ./scripts/docker/docker-test.sh

# Clean up build artifacts
clean:
    @echo "Cleaning up build artifacts..."
    rm -rf build/
    rm -rf dist/
    rm -rf *.egg-info/
    find . -type d -name __pycache__ -exec rm -rf {} + || true
    find . -type f -name "*.pyc" -delete
    find . -type f -name "*.pyo" -delete
    find . -type f -name "*.pyd" -delete
    find . -type f -name ".coverage" -delete
    find . -type d -name "*.egg-info" -exec rm -rf {} + || true
    find . -type d -name "*.egg" -exec rm -rf {} + || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + || true
    find . -type d -name "htmlcov" -exec rm -rf {} + || true

# Watch unit tests and re-run on change (requires watchexec)
# `--` keeps pytest flags from being eaten by just.
watch-test *ARGS:
    watchexec -e py -r -- just test-unit -- {{ ARGS }}

# Show lintro version and available tools
info:
    @uv run lintro --version
    @echo ""
    @uv run lintro tools

# Astro docs site dev server (apps/site)
site-dev:
    ./scripts/ci/site/dev.sh

# Build the docs site (+ Pagefind index)
site-build:
    uv run python scripts/ci/site/migrate-docs-content.py
    ./scripts/ci/site/build.sh

# Run docs site checks and tests
site-test:
    ./scripts/ci/site/check.sh
    ./scripts/ci/site/test.sh

# Preview the built docs site
site-preview: site-build
    ./scripts/ci/site/preview-serve.sh
