# Agent Dev Environment Guide

Lintro is a single-product **Python CLI** (package `lintro`, managed with `uv`). It
wraps many third-party linters/formatters and runs as a command that exits — there is
**no long-running service, server, or database** to start. "Running the app" means
invoking the `lintro` CLI (e.g. `uv run lintro check .`, `uv run lintro format .`).

## Cursor Cloud specific instructions

The Cursor Cloud update script runs `uv sync --dev --extra full`, which creates `.venv`
with all Python dependencies plus the Python-based wrapped tools (ruff, black, mypy,
bandit, pydoclint, yamllint). `uv` is installed at `~/.local/bin` (add it to `PATH` if a
fresh shell can't find `uv`). `just` is **not** preinstalled on Cloud VMs; install it
only if you want the justfile wrappers (see `docs/contributing.md`). After the update
script, use the `uv run` commands below.

## Running / linting / testing / building

Standard commands live in the `justfile` and `docs/contributing.md`. All commands run
through `uv` (do not call bare `python`/`pytest`). Cloud-primary commands (no `just`
required):

- Run the CLI: `uv run lintro check .`, `uv run lintro format .`,
  `uv run lintro list-tools`.
- Lint: `uv run lintro check .` (or `just lint` after installing `just`; that also runs
  mypy first).
- Type-check only: `uv run lintro check . --tools mypy` (or `just mypy`).
- Test: `uv run pytest` (or `just test` after installing `just`).
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

## Package layering (#2290)

Two `import-linter` contracts in `[tool.importlinter]` enforce import direction:
**`core-does-not-import-ai`** (nothing outside `ai`, `mcp`, `cli_utils`, `api` may
import `lintro.ai`) and **`layers`**, highest first — `cli_utils | mcp | api`, `ai`,
`watch | profiling`, `plugins | tools`, `config`, `formatters | parsers`, `utils`,
`models`, `enums`, `exceptions | licenses | deps`. Packages on one line may not import
each other. Check with `uv run lintro chk . --tools import-linter`.

**Ratchet rule.** Every current violation is an `ignore_imports` entry tagged with the
issue that removes it. Entries may be **deleted, never added**, and a PR that closes a
cycle deletes its entries. Lazy (function-body) imports are not a fix: `import-linter`
and `scripts/ci/import_matrix.py` both count them, and
`tests/unit/test_import_boundaries.py` pins the two-way cycle count.

## Docs site (optional secondary product)

`apps/site` is an Astro + Pagefind docs site built with `bun`. After installing `just`,
use `just site-dev` / `just site-build`; otherwise run `./scripts/ci/site/dev.sh` and
`./scripts/ci/site/build.sh`. It is not required to develop or test the CLI.
