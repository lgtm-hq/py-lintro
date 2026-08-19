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
#   - repo .venv with lintro plus the tools the selected suite needs
#     (uv sync --dev --extra full)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIXTURE_DIR="${REPO_ROOT}/benchmarks/fixtures/small-python"
RESULTS_DIR="${HYPERFINE_RESULTS_DIR:-${REPO_ROOT}/benchmarks/results/hyperfine}"
SEQUENTIAL_SCRIPT="${REPO_ROOT}/benchmarks/hyperfine/sequential-ruff-mypy.sh"
RUN_IN_DIR="${REPO_ROOT}/benchmarks/hyperfine/run-in-dir.sh"
VALID_SUITES=(all ruff mypy format multi)

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

suite_is_valid=0
for valid in "${VALID_SUITES[@]}"; do
	if [[ "${SUITE}" == "${valid}" ]]; then
		suite_is_valid=1
		break
	fi
done
if ((suite_is_valid == 0)); then
	echo "error: invalid --suite '${SUITE}' (expected: ${VALID_SUITES[*]})" >&2
	usage >&2
	exit 2
fi

if ((QUICK)); then
	WARMUP=1
	RUNS=3
fi

should_run() {
	local name="$1"
	[[ "${SUITE}" == "all" || "${SUITE}" == "${name}" ]]
}

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

PATH="${REPO_ROOT}/.venv/bin:${HOME}/.local/bin:${PATH}"
export PATH
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

if [[ ! -x "${RUN_IN_DIR}" ]]; then
	echo "error: chdir helper is not executable: ${RUN_IN_DIR}" >&2
	exit 1
fi

if [[ ! -d "${FIXTURE_DIR}/src" ]]; then
	echo "error: fixture not found: ${FIXTURE_DIR}" >&2
	exit 1
fi

LINTRO_BIN="${REPO_ROOT}/.venv/bin/lintro"
if [[ ! -x "${LINTRO_BIN}" ]]; then
	LINTRO_BIN="$(command -v lintro || true)"
fi
if [[ -z "${LINTRO_BIN}" ]]; then
	echo "error: lintro is not installed in .venv or on PATH" >&2
	echo "hint: from the repo root run: uv sync --dev --extra full" >&2
	exit 127
fi

need_ruff=0
need_mypy=0
need_sequential=0
if should_run ruff || should_run format || should_run multi; then
	need_ruff=1
fi
if should_run mypy || should_run multi; then
	need_mypy=1
fi
if should_run multi; then
	need_sequential=1
fi

missing=()
if ((need_ruff)); then
	command -v ruff >/dev/null 2>&1 || missing+=("ruff")
fi
if ((need_mypy)); then
	command -v mypy >/dev/null 2>&1 || missing+=("mypy")
fi
if ((${#missing[@]} > 0)); then
	echo "error: missing tools on PATH: ${missing[*]}" >&2
	echo "hint: from the repo root run: uv sync --dev --extra full" >&2
	exit 127
fi

if ((need_sequential)) && [[ ! -x "${SEQUENTIAL_SCRIPT}" ]]; then
	echo "error: sequential helper is not executable: ${SEQUENTIAL_SCRIPT}" >&2
	exit 1
fi

mkdir -p "${RESULTS_DIR}"

# Resolve absolute binaries once so --shell=none never depends on a login shell.
RUFF_BIN="$(command -v ruff || true)"
MYPY_BIN="$(command -v mypy || true)"

# Invoke the installed lintro binary (not ``uv run``) so measured overhead is
# orchestration, not uv's project resolver. Extra ruff stages are disabled so
# the timed work matches the direct ``ruff check`` / ``ruff format`` commands.
LINTRO_CHK=(
	"${RUN_IN_DIR}" "${FIXTURE_DIR}"
	"${LINTRO_BIN}" chk --yes
	--tool-options ruff:format_check=False
)
LINTRO_FMT=(
	"${RUN_IN_DIR}" "${FIXTURE_DIR}"
	"${LINTRO_BIN}" fmt --yes
	--tool-options ruff:lint_fix=False
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
        'lintro_invocation': '.venv/bin/lintro (chdir via run-in-dir.sh)',
        'cwd': 'benchmarks/fixtures/small-python (via run-in-dir.sh)',
        'notes': [
            'Fixture pyproject disables post_checks so single-tool runs are isolated.',
            'Direct tools use the repo .venv binaries on PATH.',
            'lintro ruff check disables format_check; fmt disables lint_fix so the timed work matches the direct binary.',
            'Relative overhead is most meaningful on the same machine/OS.',
        ],
    },
    'result_files': sorted(p.name for p in results_dir.glob('*-overhead.json')),
}
Path(r'''${meta_file}''').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
print('Wrote', r'''${meta_file}''')
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

ran=0
expected_json=()

if should_run ruff; then
	# Fast Rust linter — orchestration overhead is most visible here.
	# --reference already times the direct tool; do not also pass it as a
	# named command or hyperfine will duplicate the measurement.
	ref_cmd="$(join_cmd "${RUN_IN_DIR}" "${FIXTURE_DIR}" "${RUFF_BIN}" check .)"
	lintro_cmd="$(join_cmd "${LINTRO_CHK[@]}" --tools ruff .)"
	run_hyperfine "${RESULTS_DIR}/ruff-check-overhead.json" \
		--reference "${ref_cmd}" \
		--reference-name "ruff check" \
		--command-name "lintro chk --tools ruff" "${lintro_cmd}"
	expected_json+=("${RESULTS_DIR}/ruff-check-overhead.json")
	ran=1
fi

if should_run mypy; then
	# Slow type checker — relative overhead should shrink vs ruff.
	ref_cmd="$(join_cmd "${RUN_IN_DIR}" "${FIXTURE_DIR}" "${MYPY_BIN}" .)"
	lintro_cmd="$(join_cmd "${LINTRO_CHK[@]}" --tools mypy .)"
	run_hyperfine "${RESULTS_DIR}/mypy-overhead.json" \
		--reference "${ref_cmd}" \
		--reference-name "mypy" \
		--command-name "lintro chk --tools mypy" "${lintro_cmd}"
	expected_json+=("${RESULTS_DIR}/mypy-overhead.json")
	ran=1
fi

if should_run format; then
	# Formatter path. Fixture sources are already formatted (no-op write).
	ref_cmd="$(join_cmd "${RUN_IN_DIR}" "${FIXTURE_DIR}" "${RUFF_BIN}" format .)"
	lintro_cmd="$(join_cmd "${LINTRO_FMT[@]}" --tools ruff .)"
	run_hyperfine "${RESULTS_DIR}/ruff-format-overhead.json" \
		--reference "${ref_cmd}" \
		--reference-name "ruff format" \
		--command-name "lintro fmt --tools ruff" "${lintro_cmd}"
	expected_json+=("${RESULTS_DIR}/ruff-format-overhead.json")
	ran=1
fi

if should_run multi; then
	# Multi-tool orchestration vs sequential native tools.
	ref_cmd="$(join_cmd "${SEQUENTIAL_SCRIPT}")"
	lintro_cmd="$(join_cmd "${LINTRO_CHK[@]}" --tools ruff,mypy .)"
	run_hyperfine "${RESULTS_DIR}/multi-tool-overhead.json" \
		--reference "${ref_cmd}" \
		--reference-name "sequential ruff && mypy" \
		--command-name "lintro chk --tools ruff,mypy" "${lintro_cmd}"
	expected_json+=("${RESULTS_DIR}/multi-tool-overhead.json")
	ran=1
fi

if ((ran == 0)); then
	echo "error: no suites ran for --suite '${SUITE}'" >&2
	exit 1
fi

write_baseline_meta

echo
echo "Done. JSON results:"
ls -1 "${expected_json[@]}" "${RESULTS_DIR}/baseline-meta.json"
echo
echo "Tip: open benchmarks/README.md for how to interpret relative overhead."
