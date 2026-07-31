# Agent Dev Environment Guide

Lintro is a single-product **Python CLI** (package `lintro`, managed with `uv`). It
wraps many third-party linters/formatters and runs as a command that exits — there is
**no long-running service, server, or database** to start. "Running the app" means
invoking the `lintro` CLI (e.g. `uv run lintro check .`, `uv run lintro format .`).

## Cursor Cloud specific instructions

The Cursor Cloud update script runs `uv sync --dev --extra full`, which creates `.venv`
with all Python dependencies plus the Python-based wrapped tools (ruff, black, mypy,
bandit, pydoclint, yamllint). `uv` is installed at `~/.local/bin` (add it to `PATH` if a
fresh shell can't find `uv`).

## Running / linting / testing / building

Standard commands live in the `Makefile` and `docs/contributing.md`; prefer those. All
commands run through `uv` (do not call bare `python`/`pytest`):

- Run the CLI: `uv run lintro check .`, `uv run lintro format .`,
  `uv run lintro list-tools`.
- Lint: `make lint` (runs `mypy` then `lintro check .`); type-check only: `make mypy`.
- Test: `uv run pytest` (or `make test`, which wraps pytest via lintro with coverage).
- Build a wheel/sdist: `uv build`.

## Non-obvious gotchas

- **`pytest.ini` sets `--maxfail=3`**, so a full `uv run pytest` run aborts after the
  first few failures. When triaging, pass `--maxfail=0` to see the full picture. The
  full **unit** suite (`uv run pytest tests/unit`) is green on a fresh VM.
- **Many wrapped tools are external (non-Python) and optional.** `uv sync` does NOT
  install prettier, hadolint, shellcheck, actionlint, oxlint, taplo, gitleaks,
  `markdownlint-cli2`, rustfmt/cargo, etc. `lintro check .` silently **skips** any tool
  missing from `PATH`, so it still passes without them — but some `tests/integration/**`
  tests assume the external tool is present and will **fail (not skip)** without it
  (e.g. `test_rustfmt_integration.py` needs a Rust toolchain; the markdownlint parity
  test needs `markdownlint-cli2` on `PATH` because `npx` alone makes it non-skip).
  Install the full set with `./scripts/utils/install-tools.sh --local` (network-heavy;
  installs into `~/.local/bin`, `~/.bun/bin`, `~/.cargo/bin` and pulls a Rust
  toolchain). This is intentionally kept out of the update script.
- **`tests/integration/test_built_package.py` needs the system `python3.12-venv`
  package.** Those wheel tests call stdlib `python -m venv`; without `ensurepip` they
  fail with "recreate your virtual environment" (not a code bug). Install once with
  `sudo apt-get install -y python3.12-venv` if missing on a fresh VM.
- `tests/unit/plugins/test_entry_point_plugins.py::test_list_tools_shows_origin_for_builtin_and_external`
  can fail under `pytest -n auto` due to plugin-registry cross-test pollution; it passes
  when run on its own. Not an environment problem.
- The interpreter is the system Python (3.12); `requires-python` is `>=3.11`.
- Set `UV_LINK_MODE=copy` to avoid uv hardlink warnings when running commands.

## Docs site (optional secondary product)

`apps/site` is an Astro + Pagefind docs site built with `bun` (`make site-dev`,
`make site-build`). It is not required to develop or test the CLI.
