# Lintro

Unified CLI tool for code formatting, linting, and quality assurance.

## Commands

- `uv run lintro chk` — Run all checks
- `uv run lintro fmt` — Format code
- `uv run lintro tst` — Run tests with coverage
- `uv run pytest` — Run tests directly

## Stack

- Python >= 3.13, managed with `uv`
- Linting with `lintro` (self-hosted)

## Standards

- See `AGENTS.md` for skill index
- Type hints and return types required on all functions
- Google-style docstrings on all modules, classes, and functions
- Trailing comma on multi-arg signatures
- Explicit kwargs: `foo(bar=bar, baz=baz)` not `foo(bar, baz)`