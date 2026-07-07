# Justfile for py-lintro development.
# Run `just --list` to see all available recipes.
#
# Install just:
#   macOS:  brew install just
#   Linux:  cargo install just
#           (or) curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash

set dotenv-load := true
set shell := ["bash", "-uc"]

# Default recipe - show help
default:
    @just --list

# Set up development environment with uv
setup:
    @echo "Setting up development environment with uv..."
    uv sync --dev --extra full
    uv pip install -e .
    @echo "Setup complete! Try 'just test' or 'just lint'"

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

# Run integration tests
test-integration:
    @echo "Running integration tests..."
    ./scripts/local/local-test.sh

# Run unit tests only (faster); pass extra pytest args after the recipe name
test-unit *ARGS:
    uv run pytest tests/unit {{ARGS}}

# Run linting with lintro (runs mypy first)
lint: mypy
    @echo "Running lintro check..."
    uv run lintro check .

# Run linting with specific tools (e.g. `just lint-tools mypy,ruff`)
lint-tools +TOOLS:
    uv run lintro check . --tools {{TOOLS}}

# Format code with lintro
format:
    @echo "Running lintro format..."
    uv run lintro format .

# Run mypy type checking via lintro
mypy:
    @echo "Running mypy type checking via lintro..."
    uv run lintro check . --tools mypy

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
    find . -type d -name __pycache__ -exec rm -rf {} +
    find . -type f -name "*.pyc" -delete
    find . -type f -name "*.pyo" -delete
    find . -type f -name "*.pyd" -delete
    find . -type f -name ".coverage" -delete
    find . -type d -name "*.egg-info" -exec rm -rf {} +
    find . -type d -name "*.egg" -exec rm -rf {} +
    find . -type d -name ".pytest_cache" -exec rm -rf {} +
    find . -type d -name "htmlcov" -exec rm -rf {} +
    find . -type d -name ".tox" -exec rm -rf {} +

# Watch unit tests and re-run on change (requires watchexec)
watch-test *ARGS:
    watchexec -e py -r -- just test-unit {{ARGS}}

# Run pre-commit checks (lint + test)
pre-commit: lint test

# Show lintro version and available tools
info:
    @uv run lintro --version
    @echo ""
    @uv run lintro tools

# Aliases for backwards compatibility
alias lintro-check := lint
alias lintro-format := format
alias chk := lint
alias fmt := format
