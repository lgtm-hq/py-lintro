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
TRACE_INTERVAL="${MEMORY_TRACE_INTERVAL:-30}"

tail_pid=""

# shellcheck disable=SC2329 # Invoked via `trap cleanup EXIT` below.
cleanup() {
	# Stop the streamer first so the sampler's final snapshot is not raced,
	# then stop the sampler. Both are idempotent and must never fail the run.
	if [[ -n "${tail_pid}" ]] && kill -0 "${tail_pid}" 2>/dev/null; then
		kill "${tail_pid}" 2>/dev/null || true
		wait "${tail_pid}" 2>/dev/null || true
	fi
	"${SCRIPT_DIR}/memory-sampler.sh" stop "${TRACE_LOG}" "${TRACE_PID_FILE}" \
		>/dev/null 2>&1 || true
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

# Stream the sampler log into this step's stdout. `tail -F` tolerates the file
# not existing yet and keeps following if it is rotated.
#
# Piping through `sed` would block-buffer: its stdout is a pipe, so up to 4KB
# of samples sit unflushed. That is fine for a clean exit and useless here --
# the samples closest to a runner kill are exactly the ones needed, and they
# would be discarded with the buffer. A read loop with printf issues one write
# per line, so every sample reaches the log as it is taken.
tail -F "${TRACE_LOG}" 2>/dev/null | while IFS= read -r trace_line; do
	printf '[mem] %s\n' "${trace_line}"
done &
tail_pid=$!

set +e
"$@"
command_status=$?
set -e

log_info "Command exited ${command_status}; memory trace follows the final snapshot"
exit "${command_status}"
