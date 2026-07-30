#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# run-with-memory-trace.sh
#
# Run a command with a live memory trace streamed to stdout (#1761).
#
# The Dogfood No-Silent-Skip Gate is repeatedly killed part-way through its
# lint with "The runner has received a shutdown signal" and
# "error waiting for container: unexpected EOF". Memory pressure is the
# obvious suspect -- 35 tools at 10 workers inside a container -- but that is
# a hypothesis, and this script exists to replace it with a measurement.
#
# Why not the build-binary pattern (#1707): that job samples to a file and
# uploads it as an artifact `if: failure()`. When the *runner* dies, later
# steps never run and no artifact is ever uploaded, so the evidence for this
# exact failure mode would be lost. Actions streams step stdout as it is
# produced, so the job log survives a runner death -- samples are therefore
# tee'd into the log of the same step that runs the command, rather than
# collected afterwards.
#
# The command's exit code is preserved, so wrapping a step never changes
# whether it passes.
#
# Usage:
#   scripts/ci/run-with-memory-trace.sh <command> [args...]
#
# Environment:
#   MEMORY_TRACE_INTERVAL  Seconds between snapshots (default: 30).
#   MEMORY_TRACE_LOG       Sampler log path (default: memory-trace.log).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Run a command with a live memory trace streamed to stdout (#1761).

Usage:
  scripts/ci/run-with-memory-trace.sh <command> [args...]

Samples are streamed into the current step's log rather than collected as an
artifact, because a runner shutdown skips later steps and would discard the
evidence for the failure this is meant to diagnose.

Environment:
  MEMORY_TRACE_INTERVAL  Seconds between snapshots (default: 30)
  MEMORY_TRACE_LOG       Sampler log path (default: memory-trace.log)

Exit code:
  The wrapped command's exit code, unchanged.
EOF
	exit 0
fi

if [[ $# -eq 0 ]]; then
	log_error "No command given. See --help."
	exit 2
fi

TRACE_LOG="${MEMORY_TRACE_LOG:-memory-trace.log}"
TRACE_PID_FILE="${TRACE_LOG}.pid"
TRACE_CURSOR_FILE="${TRACE_LOG}.cursor"
TRACE_INTERVAL="${MEMORY_TRACE_INTERVAL:-30}"

tail_pid=""

# shellcheck disable=SC2329 # Invoked via `trap cleanup EXIT` below.
cleanup() {
	# Order matters. Stopping the streamer first would discard the sampler's
	# final snapshot -- the measurement closest to a kill, and the whole point
	# of the trace -- leaving it only in a local file that a dying runner never
	# uploads. So stop the sampler first, then the streamer.
	"${SCRIPT_DIR}/memory-sampler.sh" stop "${TRACE_LOG}" "${TRACE_PID_FILE}" \
		>/dev/null 2>&1 || true
	if [[ -n "${tail_pid}" ]] && kill -0 "${tail_pid}" 2>/dev/null; then
		kill "${tail_pid}" 2>/dev/null || true
		wait "${tail_pid}" 2>/dev/null || true
	fi
	# Flush synchronously once the streamer is gone, so the sampler's final
	# snapshot reaches the job log rather than only the local file that a
	# dying runner never uploads.
	flush_trace || true
}
trap cleanup EXIT

log_info "Starting memory trace (interval ${TRACE_INTERVAL}s) -> ${TRACE_LOG}"
"${SCRIPT_DIR}/memory-sampler.sh" start \
	"${TRACE_LOG}" "${TRACE_PID_FILE}" "${TRACE_INTERVAL}" || {
	# Instrumentation must never block the thing it is measuring.
	log_warning "Memory sampler failed to start; running without a trace"
	trap - EXIT
	exec "$@"
}

# Stream new sampler output into this step's stdout.
#
# Not `tail -F ... | while read`: `$!` after a pipeline is the PID of its LAST
# element, so killing it leaves `tail -F` alive holding stdout open and the
# step never finishes. This loop runs as a single background subshell whose PID
# is exactly what `$!` reports, so cleanup can stop it deterministically.
#
# Emitting line-by-line with printf also avoids the block buffering a pipe
# through `sed` would introduce -- the samples closest to a kill are the ones
# that matter, and they would sit unflushed.
#
# The cursor lives in a file, not a variable. `stream_trace` runs in a
# background subshell, so any progress it recorded in a shell variable would be
# invisible to the parent's cleanup flush -- which would then re-emit the whole
# trace from line 1 and duplicate every sample already in the job log. Only one
# of the two reads the cursor at a time: cleanup kills the streamer before
# flushing, so there is no concurrent writer.
printf '0\n' >"${TRACE_CURSOR_FILE}"

# shellcheck disable=SC2329 # Invoked from cleanup() and stream_trace().
emit_new_trace_lines() {
	local cursor emitted=0
	cursor="$(cat "${TRACE_CURSOR_FILE}" 2>/dev/null || echo 0)"
	cursor="${cursor//[[:space:]]/}"
	[[ "${cursor}" =~ ^[0-9]+$ ]] || cursor=0
	# Advance the cursor by what was actually printed, never by a separately
	# measured line count. The sampler appends while we read, so a `wc -l`
	# taken before `tail` under-reports what `tail` then emits, and the
	# difference is replayed on the next pass. Counting inside the loop makes
	# the cursor exact by construction.
	#
	# Process substitution, not a pipe: a piped `while` runs in a subshell and
	# its counter would be lost.
	while IFS= read -r trace_line; do
		printf '[mem] %s\n' "${trace_line}"
		emitted=$((emitted + 1))
	done < <(tail -n "+$((cursor + 1))" "${TRACE_LOG}" 2>/dev/null)
	if [[ "${emitted}" -gt 0 ]]; then
		printf '%s\n' "$((cursor + emitted))" >"${TRACE_CURSOR_FILE}"
	fi
}

# shellcheck disable=SC2329 # Invoked from cleanup(), which runs via trap.
flush_trace() {
	emit_new_trace_lines
}

stream_trace() {
	while :; do
		emit_new_trace_lines
		sleep 2
	done
}

stream_trace &
tail_pid=$!

set +e
"$@"
command_status=$?
set -e

log_info "Command exited ${command_status}; memory trace follows the final snapshot"
exit "${command_status}"
