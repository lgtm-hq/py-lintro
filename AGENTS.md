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

- **The full unit suite (`uv run pytest tests/unit`) is green on a fresh VM.** Pytest is
  configured in one place, `[tool.pytest.ini_options]` in `pyproject.toml`, and sets no
  `--maxfail`, so a red run reports every failure.
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
- **The plugin registry is snapshotted around every test.** `tests/conftest.py` holds an
  autouse `_isolate_plugin_registry` fixture that saves and restores
  `ToolRegistry._tools` / `._instances` / `._origins`, so registering or clearing a
  plugin cannot leak into the next test (#2315). Tests must not add their own
  save/restore blocks. The suite also runs under `pytest-randomly`, so nothing may
  depend on declaration order.
- **Two test-hygiene scanners run as tests.**
  `scripts/ci/testing/scan_duplicate_test_bodies.py` reports test functions sharing a
  normalised body and module context; `scripts/ci/testing/scan_mock_only_tests.py`
  reports tests whose only assertions read mock call bookkeeping (`assert_called*`,
  `call_count`, …). Both are ratcheted at **0** by
  `tests/scripts/ci/testing/test_suite_hygiene_scanners.py` — a new test must assert on
  something observable, and must not be a copy of an existing one.
- **The required `lintro-code-quality` check fails closed (#2296).** If the dogfooding
  lint job is killed, cancelled, or times out it produces no lint verdict, and the gate
  goes **red** with `status=no-verdict` / `infra-flake=true` rather than absorbing the
  noise. A red check whose job summary says "No lint verdict (runner loss); auto-rerun
  will retry" is runner loss, not a lint violation — `auto-rerun-on-infra-failure.yml`
  reruns it up to three times. Do not "fix" that red by re-greening the gate: wait for
  the rerun, or find out why the runner keeps dying.
- The interpreter is the system Python (3.12); `requires-python` is `>=3.11`. CI's
  compatibility matrix runs the suite on 3.11, 3.12, 3.13 and 3.14; the coverage job
  runs on 3.14 alone (`.github/workflows/test-ci.yml`).
- **`[dependency-groups] dev` in `pyproject.toml` is the only dev dependency list**
  (#2314; the sibling `ai-runtime` group is a dependency fix-up, not a dev list, and a
  default group so it needs no flag — see below). There is no `dev` or `test` extra:
  `uv sync --dev` (uv syncs the `dev` group by default) installs the test toolchain —
  pytest and its plugins, assertpy, ruff, black, mypy, bandit, yamllint. Use
  **`uv sync --dev --extra full`** to match CI, which requests `extras: 'full'` in
  `test-ci.yml`; the `full` extra adds the dogfooding linters the group does not carry
  (pylint, pydoclint, import-linter). The remaining extras are runtime-facing: `tools`,
  `ai`, `mcp`, `typing`.
- **`pyproject.toml` drops anthropic's `docstring-parser` requirement on purpose**
  (#2378). pydoclint (the `full` extra) needs `docstring-parser-fork`; anthropic (the
  `ai` extra) needs `docstring-parser`. Both distributions install the same top-level
  `docstring_parser` package, so an environment carrying both — the `ai` Docker image
  and any `--extra full --extra ai` sync — ends up with one shadowing the other, and
  pydoclint dies on import with
  `cannot import name 'DocstringYields' from 'docstring_parser.common'`. `lintro chk`
  then reports pydoclint as **skipped**, exits 0, and silently stops running the DOC
  gate. No version pin fixes it: those symbols exist only in the fork.
  `[tool.uv] override-dependencies` therefore marker-disables anthropic's requirement
  and lets the fork, a superset, own the module — anthropic touches only
  `docstring_parser.parse` / `.Docstring`, in the optional `@beta_tool` helper lintro
  does not use. Because the override is global, `uv sync --extra ai` without `full`
  would install neither distribution and `anthropic.lib.tools` would raise
  `ModuleNotFoundError`, so the uv-only `ai-runtime` dependency-group puts the fork
  back. It sits in `[tool.uv] default-groups` next to `dev`, so every sync picks it up
  and no call site opts in — `ai-review.yml` runs under `pull_request_target`, taking
  the workflow from the base branch while checking out `base.sha`, so a required flag
  would skew against an older base. The fork stays off the published `ai` extra on
  purpose: pip never reads `[tool.uv]`, so shipping it in wheel metadata would make
  `uv pip install 'lintro[ai]'` install two distributions owning `docstring_parser`.
  Never re-add `docstring-parser` to the resolution:
  `tests/unit/test_docstring_parser_override.py` fails if it comes back, and the `ai`
  Docker stage smoke-tests `pydoclint --version` because it is the only build that
  installs `full` and `ai` together.
- Set `UV_LINK_MODE=copy` to avoid uv hardlink warnings when running commands.

## Structural lint thresholds

Ruff enforces shape as well as style: `C90` (max-complexity 15), `PLR0913` (max-args 8),
`PLR0912` (max-branches 12) and `PLR0915` (max-statements 50), plus `RUF100` so dead
`# noqa` pragmas cannot accumulate. Functions that exceeded a threshold when the
families were switched on are recorded as per-file ignores in `pyproject.toml` under the
`# --- structural baseline ---` block. That block is a burn-down list owned by issues
`#2311`, `#2313`, `#1972` and `#1995` — delete entries as the refactors land, never add
one, and never raise a threshold. The guard test `tests/unit/test_ruff_baseline.py`
fails if the baseline grows.

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
`tests/unit/test_import_boundaries.py` pins the two-way cycle count. Exception: a new
tool definition added under the current definitions pattern records its three
`-> lintro.plugins.{base,protocol,registry}` edges under #2311, which deletes them all
when it factors the pattern.

## Duplicate code (#2293)

`pylint`'s `duplicate-code` checker (`R0801`) runs on the tool-definition modules only —
the scope comes from `[tool.lintro.pylint] include` in `pyproject.toml`, which lists
`lintro/tools/definitions` plus every per-tool `lintro/tools/<name>` package #2311 has
moved a definition into, and also records `duplicate_code_baseline`, the number of clone
sets present when the gate landed. When a tool moves out of `lintro/tools/definitions`,
add its package to `include` in the same PR so the gate's scope follows it. pylint has
no per-finding baseline, so the ratchet is a count: `lintro/utils/duplicate_code.py`
takes the `R0801` findings out of the pylint result and fails the run only when the
count is **higher** than the baseline. **The baseline may only shrink** — lower it in
the pull request that removes duplication, never raise it, and never raise
`min-similarity-lines`. #2311 factors the definition template and is done when the
number reaches 0; `tests/unit/test_duplicate_code_baseline.py` fails if the baseline
grows above the ceiling recorded there, or if a live pylint run reports **more**
findings than the baseline. That live check is `<=`, not `==`: the `R0801` count depends
on the resolved pylint/astroid build as well as on the code (the same tree reported 34
on one CI interpreter and 33 on another, #2365), and a count below the baseline is the
prompt to lower the baseline, not a failure.

## Docs site (optional secondary product)

`apps/site` is an Astro + Pagefind docs site built with `bun`. After installing `just`,
use `just site-dev` / `just site-build`; otherwise run `./scripts/ci/site/dev.sh` and
`./scripts/ci/site/build.sh`. It is not required to develop or test the CLI.
