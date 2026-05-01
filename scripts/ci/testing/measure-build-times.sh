#!/usr/bin/env bash
# Phase 1 (#878) build-time measurement helper.
#
# Emits one JSON line per GitHub Actions run for downstream comparison of
# tools-image / py-lintro main / py-lintro base build durations.
#
# Usage:
#   scripts/ci/testing/measure-build-times.sh <run_id> [run_id...]
#
# Each line: {run_id, event, head_branch, conclusion, tools_seconds,
#             main_seconds, base_seconds, cache_state}
# cache_state is "cold" / "warm" / "fallback" / "unknown" derived from the
# docker-build job log scan for "importing cache manifest from".
#
# Requires: gh, jq.

set -euo pipefail

print_help() {
	cat <<'EOF'
Usage: measure-build-times.sh [--help] <run_id> [run_id...]

Emit one JSON line per GitHub Actions run with tools-image / py-lintro main /
py-lintro base build durations and inferred cache hit state. Used to populate
the Phase 1 (#878) measurement table.

Arguments:
  <run_id>   GitHub Actions run id (numeric). Multiple ids accepted.

Options:
  --help     Show this help text and exit.

Output (one JSON line per run):
  {run_id, event, head_branch, conclusion,
   tools_seconds, main_seconds, base_seconds, cache_state}

cache_state is "cold" / "warm" / "fallback" / "unknown" derived from the
docker-build job log scan for "importing cache manifest from". "fallback"
means at least one ref missed but another imported successfully (e.g. a
PR cold-start that warmed from :main).

Requires: gh, jq.
EOF
}

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
	print_help
	exit 0
fi

if [[ $# -lt 1 ]]; then
	print_help >&2
	exit 64
fi

duration_seconds() {
	# stdin: ISO start \t ISO end. Emits integer seconds, or "null" on bad input.
	awk -F '\t' '
	{
		s = $1; e = $2;
		if (s == "" || e == "" || s == "null" || e == "null") { print "null"; next }
		cmd_s = "date -u -d \"" s "\" +%s 2>/dev/null || gdate -u -d \"" s "\" +%s 2>/dev/null"
		cmd_e = "date -u -d \"" e "\" +%s 2>/dev/null || gdate -u -d \"" e "\" +%s 2>/dev/null"
		cmd_s | getline si; close(cmd_s)
		cmd_e | getline ei; close(cmd_e)
		if (si == "" || ei == "") { print "null"; next }
		print (ei - si)
	}'
}

step_seconds() {
	# Per-step duration, scoped to a job. job-level timings include setup
	# (Harden Runner, checkout, login, ...) and would dwarf the actual build.
	local run_id="$1" job_substr="$2" step_substr="$3"
	gh run view "$run_id" --json jobs \
		--jq "
			.jobs[]
			| select(.name | contains(\"${job_substr}\"))
			| .steps[]
			| select(.name | contains(\"${step_substr}\"))
			| [.startedAt, .completedAt]
			| @tsv" |
		head -n 1 |
		duration_seconds
}

cache_state_for_run() {
	# Successful import wins over "not found": a PR cold-start that hits the
	# `:pr-<N>` ref but falls back to `:main` reports "fallback", not "cold".
	#   warm     — at least one ref imported, none missed
	#   fallback — at least one ref imported AND at least one missed
	#   cold     — all attempted refs missed
	#   unknown  — no import attempts logged
	local run_id="$1"
	local log
	log=$(gh run view "$run_id" --log 2>/dev/null || true)
	if [[ -z "$log" ]]; then
		echo "unknown"
		return
	fi
	local missed=0 imported=0
	grep -q 'importing cache manifest from .* not found' <<<"$log" && missed=1
	# Successful import: "importing cache manifest from <ref>" without
	# "not found" on the same line.
	grep 'importing cache manifest from' <<<"$log" |
		grep -vq 'not found' && imported=1
	if ((imported && missed)); then
		echo "fallback"
	elif ((imported)); then
		echo "warm"
	elif ((missed)); then
		echo "cold"
	else
		echo "unknown"
	fi
}

for run_id in "$@"; do
	meta=$(gh run view "$run_id" --json event,headBranch,conclusion --jq \
		'{event: .event, head_branch: .headBranch, conclusion: .conclusion}')
	tools_s=$(step_seconds "$run_id" "Build Tools Image" "Build tools image" || echo null)
	main_s=$(step_seconds "$run_id" "Build Docker Images" "Build Docker image (py-lintro:latest)" || echo null)
	base_s=$(step_seconds "$run_id" "Build Docker Images" "Build Base Docker image" || echo null)
	cache=$(cache_state_for_run "$run_id")
	jq -nc \
		--arg run_id "$run_id" \
		--argjson tools "${tools_s:-null}" \
		--argjson main "${main_s:-null}" \
		--argjson base "${base_s:-null}" \
		--arg cache "$cache" \
		--argjson meta "$meta" \
		'{run_id: $run_id, event: $meta.event, head_branch: $meta.head_branch,
		  conclusion: $meta.conclusion, tools_seconds: $tools,
		  main_seconds: $main, base_seconds: $base, cache_state: $cache}'
done
