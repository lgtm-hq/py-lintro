# AGENTS.md

## Cursor Cloud specific instructions

Lintro is a single-product **Python CLI** (package `lintro`, managed with `uv`). It
wraps many third-party linters/formatters and runs as a command that exits — there is
**no long-running service, server, or database** to start. "Running the app" means
invoking the `lintro` CLI.

### Environment already provided by the update script

The update script runs `uv sync --dev --extra full`, which creates `.venv` with all
Python dependencies and the Python-based wrapped tools (ruff, black, mypy, bandit,
pydoclint, yamllint). `uv` lives in `~/.local/bin`, which (together with `~/.bun/bin`)
is on `PATH` via `~/.bashrc`.

### Running / linting / testing / building

All commands run through `uv` (do not call bare `python`/`pytest`). Standard targets are
already defined in the `Makefile` and `docs/contributing.md`; prefer those:

- Run the CLI: `uv run lintro check .`, `uv run lintro format .`,
  `uv run lintro list-tools`.
- Lint: `make lint` (runs `mypy` then `lintro check .`); type-check only: `make mypy`.
- Test (with coverage): `make test`, or `./scripts/local/run-tests.sh`, or
  `uv run pytest`. `pytest.ini` always enables coverage (term/html/xml).
- Build a wheel/sdist: `uv build`.

### Non-obvious gotchas

- **External (non-Python) tools are optional.** `uv sync` does NOT install prettier,
  hadolint, shellcheck, actionlint, oxlint, taplo, gitleaks, etc. `lintro check .`
  silently **skips** any tool missing from `PATH`, so it still passes without them. To
  exercise every integration, install them with
  `./scripts/utils/install-tools.sh --local` (network-heavy; installs into
  `~/.local/bin`, `~/.bun/bin`, and `~/.cargo/bin`, and pulls a Rust toolchain). This is
  intentionally kept out of the update script.
- **`tests/integration/test_built_package.py` needs the system `python3.12-venv`
  package.** Those two wheel tests call stdlib `python -m venv`; without `ensurepip`
  they fail with "ensurepip is not available" (not a code bug). Install once with
  `sudo apt-get install -y python3.12-venv` if it is ever missing on a fresh VM.
- The interpreter is the system Python (3.12); `requires-python` is `>=3.11`.
- Set `UV_LINK_MODE=copy` to avoid uv hardlink warnings when running tests.
