.PHONY: setup install test test-integration lint format bench clean help site-dev site-build site-test site-preview

# Include .env file if it exists
-include .env

# Documentation site (Astro + Pagefind)
-include scripts/ci/site/defaults.env
SITE_ASTRO_BASE ?= $(ASTRO_BASE_DEFAULT)

# Default target
all: setup test

# Setup development environment
setup:
	@echo "Setting up development environment with uv..."
	uv sync --dev --extra full
	uv pip install -e .
	@echo "Setup complete! Try 'make test' or 'make lintro-check'"

# Install the package
install:
	@echo "Installing package with uv..."
	uv sync --dev --extra full
	uv pip install -e .

# Run all tests
test: mypy
	@echo "Running tests with coverage..."
	uv run lintro tst tests/ --tool-options pytest:coverage_report=True,pytest:coverage_html=htmlcov,pytest:coverage_xml=coverage.xml,pytest:timeout=600
	@echo "Coverage reports generated:"
	@echo "  - Terminal: displayed above"
	@echo "  - HTML: htmlcov/index.html"
	@echo "  - XML: coverage.xml"

# Run integration tests using our local-test.sh script
test-integration:
	@echo "Running integration tests..."
	./scripts/local-test.sh

# Run linting using lintro itself
lint: mypy
	@echo "Running lintro check..."
	uv run lintro check .

# Format code using lintro itself
format:
	@echo "Running lintro format..."
	uv run lintro format .

# Run lintro check (alias for lint)
lintro-check: lint

# Run lintro format (alias for format)
lintro-format: format

# Build Docker image
docker-build:
	@echo "Building Docker image..."
	docker build --target full -t py-lintro:latest .

# Run tests in Docker
docker-test:
	@echo "Running tests in Docker..."
	./scripts/docker/docker-test.sh

# Run type checking
mypy:
	@echo "Running mypy type checking via lintro..."
	uv run lintro check . --tools mypy

# Hyperfine CLI overhead benchmarks (lintro vs direct tools). See #598.
# Requires hyperfine on PATH: brew/cargo install hyperfine
bench:
	@echo "Running hyperfine CLI overhead benchmarks..."
	./benchmarks/run-hyperfine.sh

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

site-dev:
	cd apps/site && bun install && ASTRO_BASE="$(SITE_ASTRO_BASE)" bun run dev

site-build:
	uv run python scripts/ci/site/migrate-docs-content.py
	./scripts/ci/site/build.sh

site-test:
	./scripts/ci/site/check.sh
	./scripts/ci/site/test.sh

site-preview: site-build
	./scripts/ci/site/preview-serve.sh

# Show help
help:
	@echo "Available targets:"
	@echo "  setup           - Set up development environment"
	@echo "  install         - Install package only"
	@echo "  test            - Run unit tests with coverage"
	@echo "  test-integration- Run integration tests"
	@echo "  lint            - Run lintro check"
	@echo "                    - Runs mypy before lintro"
	@echo "  format          - Run lintro format"
	@echo "  mypy            - Run type checking"
	@echo "  bench           - Run hyperfine CLI overhead benchmarks (#598)"
	@echo "  docker-build    - Build Docker image"
	@echo "  docker-test     - Run tests in Docker"
	@echo "  clean           - Clean up build artifacts"
	@echo "  site-dev        - Astro docs site dev server"
	@echo "  site-build      - Build docs site (+ Pagefind index)"
	@echo "  site-test       - Run docs site checks and tests"
	@echo "  site-preview    - Preview built docs site"
	@echo "  help            - Show this help message" 