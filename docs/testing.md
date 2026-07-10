# Testing Guide

This guide explains how Lintro's test suite is organised, how tests map to the source
they cover, and the conventions to follow when adding tests. It is aimed at new
contributors who need to know where a test belongs and why some tests are skipped
locally.

For architectural context on the testing strategy, see the "Testing Strategy" section of
[`architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md). For the general
contribution flow, see [`contributing.md`](contributing.md).

## How tests map to source

Test discovery relies on convention, not on registration. Two conventions do the
mapping:

### Unit tests mirror the package layout

`tests/unit/` mirrors the structure of the `lintro/` package. A module lives at
`tests/unit/<mirrored_path>/test_<name>.py`. For example:

| Source module            | Test module                   |
| ------------------------ | ----------------------------- |
| `lintro/parsers/ruff/`   | `tests/unit/parsers/...`      |
| `lintro/formatters/`     | `tests/unit/formatters/...`   |
| `lintro/plugins/base.py` | `tests/unit/plugins/base/...` |
| `lintro/ai/`             | `tests/unit/ai/...`           |

When you add a module under `lintro/`, add its unit tests under the matching path in
`tests/unit/`. Keep unit tests isolated and fast: mock external dependencies and avoid
invoking real tool binaries.

### Integration tests are per tool

`tests/integration/` holds end-to-end tests that exercise real tool execution. These
follow a per-tool naming convention, `test_<tool>_integration.py` (for example
`test_ruff_integration.py`, `test_bandit_integration.py`,
`test_markdownlint_integration.py`). Cross-cutting end-to-end behaviour (parallel
execution, built-package smoke tests, doc-URL checks) lives in dedicated files alongside
them.

Put a test in `tests/integration/` when it needs a real tool binary or exercises the
full check/format pipeline; put it in `tests/unit/` otherwise.

## Test markers

Markers are declared in `pytest.ini` and enforced with `--strict-markers`, so an unknown
marker fails the run. The available markers are:

| Marker                                                                             | Meaning                                         |
| ---------------------------------------------------------------------------------- | ----------------------------------------------- |
| `unit`                                                                             | Isolated unit test                              |
| `integration`                                                                      | End-to-end / integration test                   |
| `docker_only`                                                                      | Requires Docker or Docker-specific dependencies |
| `slow`                                                                             | Slow-running test                               |
| `timeout`                                                                          | Overrides the default per-test timeout          |
| `cli`, `utils`                                                                     | CLI and utility test groupings                  |
| `formatters`                                                                       | Formatter test grouping                         |
| `markdownlint`, `hadolint`, `prettier`, `yamllint`, `ruff`, `actionlint`, `pytest` | Tool-specific groupings                         |

## Binary-gated tests

Integration tests depend on external tool binaries that may not be installed on a
contributor's machine. Rather than failing, these tests **skip** when the tool is
absent, so the suite stays green locally while still running fully in CI and Docker
(where every tool is installed).

Two patterns implement this gating:

- A `skipif` guard on the test, for example
  `@pytest.mark.skipif(shutil.which("bandit") is None, reason=...)`.
- The shared `skip_if_tool_unavailable` fixture in `tests/integration/conftest.py`,
  which calls `pytest.skip()` when `shutil.which(tool_name)` returns `None`.

Some tools resolve through `npx`/`bunx` (for example markdownlint-cli2), so the guard
checks for the launcher rather than a bare binary. When you add an integration test that
shells out to a tool, gate it with one of these patterns.

## Fixtures

### The conftest hierarchy

Fixtures are shared through a hierarchy of `conftest.py` files, resolved by pytest from
the test's directory upward:

- `tests/conftest.py` — project-wide fixtures (`temp_dir`, the Click `CliRunner`,
  sample-staging helpers like `ruff_violation_file`, `skip_config_injection`, and
  similar).
- `tests/integration/conftest.py` — integration-only fixtures, including
  `skip_if_tool_unavailable`.
- Package-local `conftest.py` files add fixtures scoped to a subtree.

Prefer the most local `conftest.py` that still makes the fixture available to every test
that needs it.

### Sample files and the staging pattern

Reusable sample inputs live under `test_samples/`, organised by ecosystem and tool
(`test_samples/tools/python/ruff/`, `.../javascript/prettier/`, and so on), plus shared
`test_samples/fixtures/`. Naming follows `{tool}_violations.{ext}` for
intentional-violation samples and `{tool}_clean.{ext}` for files that should pass. See
[`test_samples/README.md`](../test_samples/README.md) for the full layout.

Tests do not run tools directly against `test_samples/`. Instead they **stage** a copy
into a temporary directory and run against that copy. For example the
`ruff_violation_file` fixture copies
`test_samples/tools/python/ruff/ruff_e501_f401_violations.py` into `temp_dir` and
returns the staged path. Staging keeps the checked-in samples pristine and isolates each
test run.

### Why `test_samples/` is in `.lintro-ignore`

`test_samples/` is deliberately full of intentional violations, so Lintro must not lint
it when dogfooding on its own repository. The repository-root `.lintro-ignore` lists
`test_samples/` for exactly this reason. This is why tests that _do_ want to scan a
sample clear the tool's exclude patterns explicitly (for example, the markdownlint
parity test sets `tool.exclude_patterns = []` before checking a sample file).

## Coverage expectations

Coverage is measured against the `lintro` package. `pytest.ini` configures
`--cov=lintro` with term-missing, HTML, and XML reports. In CI the coverage job enforces
a minimum: `coverage-threshold: 80` in
[`.github/workflows/test-ci.yml`](../.github/workflows/test-ci.yml). The architecture
docs set a higher aspirational target (≥ 70% floor, 90% goal per
[`architecture/README.md`](architecture/README.md)).

Check coverage locally with:

```bash
uv run pytest --cov=lintro --cov-report=term-missing
```

## Running the suite

Use the project runner, which sets up the environment and picks appropriate tests for
the host:

```bash
# Full suite (sets up the venv, checks tool availability)
./scripts/local/run-tests.sh

# Directly via uv
uv run pytest

# A subset
uv run pytest tests/unit/tools/ -v
```

### Known environment-only local failures

A small number of tests are sensitive to the local environment and may fail on a
developer machine while passing in CI/Docker. These are known and not caused by your
changes:

- `test_markdownlint_direct_vs_lintro_parity`
  (`tests/integration/test_markdownlint_integration.py`) — compares direct
  markdownlint-cli2 output against Lintro's wrapper and depends on the exact local
  markdownlint-cli2 resolution.
- `test_prepare_execution_version_check_fails_returns_early_result`
  (`tests/unit/plugins/base/test_execution.py`) — depends on tool-version resolution
  behaviour that can differ locally.

If these are the only failures, treat the run as clean. Everything else should pass
locally.
