#!/usr/bin/env bash
# Sequential native ruff + mypy runner for hyperfine --shell=none.
#
# Hyperfine with --shell=none cannot use shell operators (&& / ;). This thin
# wrapper preserves the same argv-style process model while running the two
# tools back-to-back (never short-circuiting) and propagating the worst exit
# code, which mirrors how lintro runs every selected tool before reporting.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FIXTURE_DIR="${REPO_ROOT}/benchmarks/fixtures/small-python"
VENV_BIN="${LINTRO_BENCH_VENV_BIN:-${REPO_ROOT}/.venv/bin}"

# Prefer the repo venv binaries so PATH quirks cannot silently swap tools.
PATH="${VENV_BIN}:${HOME}/.local/bin:${PATH}"
export PATH

# ``|| true`` keeps a missing binary from aborting the assignment under
# ``set -e`` so the empty-check below can print the install hint and exit 127.
RUFF_BIN="${RUFF_BIN:-$(command -v ruff || true)}"
MYPY_BIN="${MYPY_BIN:-$(command -v mypy || true)}"

if [[ -z "${RUFF_BIN}" || -z "${MYPY_BIN}" ]]; then
	echo "error: ruff and mypy must be on PATH (run: uv sync --dev --extra full)" >&2
	exit 127
fi

cd "${FIXTURE_DIR}"

set +e
"${RUFF_BIN}" check .
ruff_status=$?
"${MYPY_BIN}" .
mypy_status=$?
set -e

if ((ruff_status > mypy_status)); then
	exit "${ruff_status}"
fi
exit "${mypy_status}"
