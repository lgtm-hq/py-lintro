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
  missing from `PATH`, so it still passes without them. `tests/integration/**` behaves
  the same way: every module that drives a wrapped tool gates on
  `tests/integration/_tools.py::require_tool` (or `require_command`, where the
  invocation comes from the plugin's own resolution), which runs the tool's version
  command and **skips** the module when the tool is absent or does not answer, and also
  when the binary is below the minimum lintro enforces. Modules with a non-tool
  prerequisite are the exception and still need it — `test_built_package.py` wants
  `python3.12-venv` (see below). Inside the tools image (`LINTRO_TOOLS_IMAGE=1`, set by
  `docker/tools.Dockerfile` and the `test-integration` compose service) only the first
  condition changes: an absent or unrunnable tool **fails** instead of skipping, because
  it is an image regression (#465). A present-but-too-old binary still **skips** even
  there — version drift is owned by the manifest gate, which tolerates Renovate lag
  (#1582). Install the full set with `./scripts/utils/install-tools.sh --local`
  (network-heavy; installs into `~/.local/bin`, `~/.bun/bin`, `~/.cargo/bin` and pulls a
  Rust toolchain). This is intentionally kept out of the update script.
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

`apps/site` is an Astro + Pagefind docs site built with `bun`. After installing `just`,
use `just site-dev` / `just site-build`; otherwise run `./scripts/ci/site/dev.sh` and
`./scripts/ci/site/build.sh`. It is not required to develop or test the CLI.
