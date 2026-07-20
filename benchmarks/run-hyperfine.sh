#!/usr/bin/env bash
# Run hyperfine CLI-overhead benchmarks for lintro vs direct tools.
#
# Implements https://github.com/lgtm-hq/py-lintro/issues/598
#
# Usage:
#   ./benchmarks/run-hyperfine.sh
#   ./benchmarks/run-hyperfine.sh --quick          # fewer runs (smoke)
#   ./benchmarks/run-hyperfine.sh --suite ruff     # one suite only
#   WARMUP=5 RUNS=20 ./benchmarks/run-hyperfine.sh
#
# Requirements:
#   - hyperfine on PATH (brew install hyperfine / cargo install hyperfine)
#   - uv + repo .venv with ruff and mypy (uv sync --dev --extra full)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIXTURE_DIR="${REPO_ROOT}/benchmarks/fixtures/small-python"
RESULTS_DIR="${REPO_ROOT}/benchmarks/results/hyperfine"
SEQUENTIAL_SCRIPT="${REPO_ROOT}/benchmarks/hyperfine/sequential-ruff-mypy.sh"

WARMUP="${WARMUP:-3}"
RUNS="${RUNS:-10}"
SUITE="all"
QUICK=0

usage() {
	cat <<'EOF'
Usage: ./benchmarks/run-hyperfine.sh [options]

Options:
  --quick              Smoke mode (warmup=1, runs=3)
  --suite NAME         One of: all, ruff, mypy, format, multi (default: all)
  --warmup N           Warmup runs (default: 3, or WARMUP env)
  --runs N             Timed runs (default: 10, or RUNS env)
  -h, --help           Show this help

Environment:
  WARMUP, RUNS         Same as --warmup / --runs
  UV_LINK_MODE         Defaults to copy when unset

JSON results are written under benchmarks/results/hyperfine/.
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--quick)
		QUICK=1
		shift
		;;
	--suite)
		SUITE="${2:?--suite requires a name}"
		shift 2
		;;
	--warmup)
		WARMUP="${2:?--warmup requires a number}"
		shift 2
		;;
	--runs)
		RUNS="${2:?--runs requires a number}"
		shift 2
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		echo "error: unknown argument: $1" >&2
		usage >&2
		exit 2
		;;
	esac
done

if ((QUICK)); then
	WARMUP=1
	RUNS=3
fi

# --- dependency checks -------------------------------------------------------

if ! command -v hyperfine >/dev/null 2>&1; then
	cat >&2 <<'EOF'
error: hyperfine is not installed or not on PATH.

Install one of:
  brew install hyperfine
  cargo install hyperfine
  # Linux (example): download a release binary from
  # https://github.com/sharkdp/hyperfine/releases

Then re-run: ./benchmarks/run-hyperfine.sh
EOF
	exit 127
fi

if ! command -v uv >/dev/null 2>&1; then
	echo "error: uv is required (https://docs.astral.sh/uv/)" >&2
	exit 127
fi

PATH="${REPO_ROOT}/.venv/bin:${HOME}/.local/bin:${PATH}"
export PATH
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

missing=()
command -v ruff >/dev/null 2>&1 || missing+=("ruff")
command -v mypy >/dev/null 2>&1 || missing+=("mypy")
if ((${#missing[@]} > 0)); then
	echo "error: missing tools on PATH: ${missing[*]}" >&2
	echo "hint: from the repo root run: uv sync --dev --extra full" >&2
	exit 127
fi

if [[ ! -d "${FIXTURE_DIR}/src" ]]; then
	echo "error: fixture not found: ${FIXTURE_DIR}" >&2
	exit 1
fi

if [[ ! -x "${SEQUENTIAL_SCRIPT}" ]]; then
	echo "error: sequential helper is not executable: ${SEQUENTIAL_SCRIPT}" >&2
	exit 1
fi

mkdir -p "${RESULTS_DIR}"

# Resolve absolute binaries once so --shell=none never depends on a login shell.
UV_BIN="$(command -v uv)"
RUFF_BIN="$(command -v ruff)"
MYPY_BIN="$(command -v mypy)"
ENV_BIN="$(command -v env)"

# lintro is invoked through uv --project so the repo package is used while the
# fixture directory is the process cwd (via env -C). That picks up the fixture
# pyproject.toml (post_checks disabled) for fair single-tool overhead.
LINTRO_CHK=(
	"${ENV_BIN}" -C "${FIXTURE_DIR}"
	"${UV_BIN}" run --project "${REPO_ROOT}"
	lintro chk --yes
)
LINTRO_FMT=(
	"${ENV_BIN}" -C "${FIXTURE_DIR}"
	"${UV_BIN}" run --project "${REPO_ROOT}"
	lintro fmt --yes
)

join_cmd() {
	# Quote argv for hyperfine's shell=none parser.
	local parts=()
	local arg
	for arg in "$@"; do
		parts+=("$(printf '%q' "${arg}")")
	done
	printf '%s' "${parts[*]}"
}

run_hyperfine() {
	local export_json="$1"
	shift
	echo
	echo "==> hyperfine → ${export_json}"
	hyperfine \
		--shell=none \
		--warmup "${WARMUP}" \
		--runs "${RUNS}" \
		--export-json "${export_json}" \
		"$@"
}

should_run() {
	local name="$1"
	[[ "${SUITE}" == "all" || "${SUITE}" == "${name}" ]]
}

write_baseline_meta() {
	local meta_file="${RESULTS_DIR}/baseline-meta.json"
	python3 -c "
import json, platform, subprocess
from datetime import datetime, timezone
from pathlib import Path

results_dir = Path(r'''${RESULTS_DIR}''')
results_dir.mkdir(parents=True, exist_ok=True)
meta = {
    'schema_version': 1,
    'suite': 'hyperfine-cli-overhead',
    'issue': 'https://github.com/lgtm-hq/py-lintro/issues/598',
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'git_sha': subprocess.check_output(
        ['git', '-C', r'''${REPO_ROOT}''', 'rev-parse', '--short', 'HEAD'],
        text=True,
    ).strip(),
    'platform': platform.platform(),
    'python_version': platform.python_version(),
    'hyperfine_version': subprocess.check_output(
        ['hyperfine', '--version'], text=True
    ).strip(),
    'warmup': int('''${WARMUP}'''),
    'runs': int('''${RUNS}'''),
    'fixture': 'small-python',
    'methodology': {
        'shell': 'none',
        'lintro_invocation': 'uv run --project <repo> lintro ...',
        'cwd': 'benchmarks/fixtures/small-python (via env -C)',
        'notes': [
            'Fixture pyproject disables post_checks so single-tool runs are isolated.',
            'Direct tools use the repo .venv binaries on PATH.',
            'Relative overhead is most meaningful on the same machine/OS.',
        ],
    },
    'result_files': sorted(p.name for p in results_dir.glob('*-overhead.json')),
}
Path(r'''${meta_file}''').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
print(f'Wrote {r'''${meta_file}'''}')
"
}

echo "hyperfine CLI overhead suite"
echo "  repo:     ${REPO_ROOT}"
echo "  fixture:  ${FIXTURE_DIR}"
echo "  results:  ${RESULTS_DIR}"
echo "  warmup:   ${WARMUP}"
echo "  runs:     ${RUNS}"
echo "  suite:    ${SUITE}"
echo "  hyperfine:$(hyperfine --version | head -n1)"

# --- suites ------------------------------------------------------------------

if should_run ruff; then
	# Fast Rust linter — orchestration overhead is most visible here.
	# --reference already times the direct tool; do not also pass it as a
	# named command or hyperfine will duplicate the measurement.
	ref_cmd="$(join_cmd "${ENV_BIN}" -C "${FIXTURE_DIR}" "${RUFF_BIN}" check .)"
	lintro_cmd="$(join_cmd "${LINTRO_CHK[@]}" --tools ruff .)"
	run_hyperfine "${RESULTS_DIR}/ruff-check-overhead.json" \
		--reference "${ref_cmd}" \
		--reference-name "ruff check" \
		--command-name "lintro chk --tools ruff" "${lintro_cmd}"
fi

if should_run mypy; then
	# Slow type checker — relative overhead should shrink vs ruff.
	ref_cmd="$(join_cmd "${ENV_BIN}" -C "${FIXTURE_DIR}" "${MYPY_BIN}" .)"
	lintro_cmd="$(join_cmd "${LINTRO_CHK[@]}" --tools mypy .)"
	run_hyperfine "${RESULTS_DIR}/mypy-overhead.json" \
		--reference "${ref_cmd}" \
		--reference-name "mypy" \
		--command-name "lintro chk --tools mypy" "${lintro_cmd}"
fi

if should_run format; then
	# Formatter path. Fixture sources are already formatted (no-op write).
	ref_cmd="$(join_cmd "${ENV_BIN}" -C "${FIXTURE_DIR}" "${RUFF_BIN}" format --check .)"
	lintro_cmd="$(join_cmd "${LINTRO_FMT[@]}" --tools ruff .)"
	run_hyperfine "${RESULTS_DIR}/ruff-format-overhead.json" \
		--reference "${ref_cmd}" \
		--reference-name "ruff format --check" \
		--command-name "lintro fmt --tools ruff" "${lintro_cmd}"
fi

if should_run multi; then
	# Multi-tool orchestration vs sequential native tools.
	ref_cmd="$(join_cmd "${SEQUENTIAL_SCRIPT}")"
	lintro_cmd="$(join_cmd "${LINTRO_CHK[@]}" --tools ruff,mypy .)"
	run_hyperfine "${RESULTS_DIR}/multi-tool-overhead.json" \
		--reference "${ref_cmd}" \
		--reference-name "sequential ruff && mypy" \
		--command-name "lintro chk --tools ruff,mypy" "${lintro_cmd}"
fi

write_baseline_meta

echo
echo "Done. JSON results:"
ls -1 "${RESULTS_DIR}"/*-overhead.json "${RESULTS_DIR}/baseline-meta.json" 2>/dev/null || true
echo
echo "Tip: open benchmarks/README.md for how to interpret relative overhead."
