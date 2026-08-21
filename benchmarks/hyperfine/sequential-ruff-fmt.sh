#!/usr/bin/env bash
# Sequential native reference for ``lintro fmt --tools ruff``.
#
# ``lintro fmt`` with ``ruff:lint_fix=False`` still runs three ruff processes:
# ``ruff check`` (to count pre-existing lint issues), ``ruff format --check``
# (to count files that would be reformatted) and finally ``ruff format``.
# Timing it against a bare ``ruff format`` would bill two extra ruff runs to
# orchestration overhead, so the reference reproduces the same stages.
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

if [[ -z "${RUFF_BIN}" ]]; then
	echo "error: ruff must be on PATH (run: uv sync --dev --extra full)" >&2
	exit 127
fi

cd "${FIXTURE_DIR}"

worst=0
record_status() {
	local status="$1"
	if ((status > worst)); then
		worst="${status}"
	fi
}

set +e
"${RUFF_BIN}" check .
record_status $?
"${RUFF_BIN}" format --check .
record_status $?
"${RUFF_BIN}" format .
record_status $?
set -e

exit "${worst}"
