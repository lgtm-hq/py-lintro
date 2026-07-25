#!/usr/bin/env bash
# memory-sampler.sh
# Background memory sampler for CI build jobs (#1707).
#
# Captures a timestamped vmstat/free (Linux) or vm_stat (macOS) snapshot every
# N seconds into a log file so an OOM suspicion around a hung Nuitka compile
# has evidence. Started before the Build binary step and stopped after it;
# the workflow uploads the log as an artifact on failure only.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../utils/utils.sh"

DEFAULT_INTERVAL="${SAMPLER_INTERVAL:-15}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Background memory sampler for CI build jobs (#1707).

Usage: memory-sampler.sh <command> [arguments]

Commands:
  snapshot                       Write a single timestamped memory snapshot to
                                 stdout and exit (used by the sampler loop and
                                 directly testable).
  start <log-file> <pid-file> [interval-seconds]
                                 Start the background sampler, appending a
                                 snapshot to <log-file> every interval seconds
                                 (default: 15, or SAMPLER_INTERVAL). The sampler
                                 PID is written to <pid-file>. Idempotent: a
                                 live sampler recorded in <pid-file> is reused.
  stop <log-file> <pid-file>     Stop the sampler recorded in <pid-file>,
                                 append a final snapshot plus stop marker to
                                 <log-file>, and remove <pid-file>. Idempotent:
                                 a missing or stale <pid-file> is a no-op.

Examples:
  memory-sampler.sh start memory-sampler.log memory-sampler.pid
  memory-sampler.sh start memory-sampler.log memory-sampler.pid 5
  memory-sampler.sh stop memory-sampler.log memory-sampler.pid
  memory-sampler.sh snapshot
EOF
	exit 0
fi

# Write one timestamped memory snapshot to stdout. Every probe is best-effort:
# a missing or failing tool degrades to a note instead of killing the sampler.
snapshot() {
	echo "=== snapshot $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
	case "$(uname -s)" in
	Linux)
		if command -v free &>/dev/null; then
			echo "--- free -m ---"
			free -m 2>/dev/null || echo "free failed (ignored)"
		else
			echo "--- free not available ---"
		fi
		if command -v vmstat &>/dev/null; then
			echo "--- vmstat ---"
			vmstat 2>/dev/null || echo "vmstat failed (ignored)"
		else
			echo "--- vmstat not available ---"
		fi
		;;
	Darwin)
		if command -v vm_stat &>/dev/null; then
			echo "--- vm_stat ---"
			vm_stat 2>/dev/null || echo "vm_stat failed (ignored)"
		else
			echo "--- vm_stat not available ---"
		fi
		;;
	*)
		echo "--- unsupported platform: $(uname -s) ---"
		;;
	esac
}

cmd_start() {
	local log_file="${1:?Log file is required}"
	local pid_file="${2:?PID file is required}"
	local interval="${3:-$DEFAULT_INTERVAL}"

	if [[ -f "$pid_file" ]]; then
		local existing_pid
		existing_pid="$(cat "$pid_file")"
		if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
			log_warning "Memory sampler already running (PID $existing_pid); reusing it"
			return 0
		fi
		log_warning "Removing stale sampler PID file ($pid_file)"
		rm -f "$pid_file"
	fi

	# Detach the loop from this step's stdio so it survives the step shell
	# exiting (each workflow `run:` block is a separate shell process).
	# Trap note: if SIGTERM lands between iterations, $! still points at the
	# completed sleep's PID and the kill no-ops — exit 0 still fires, and
	# cmd_stop escalates to SIGKILL after 5s regardless, so the gap is cosmetic.
	(
		trap 'kill $! 2>/dev/null; exit 0' TERM INT
		while :; do
			snapshot || echo "snapshot failed at $(date -u '+%Y-%m-%dT%H:%M:%SZ') (ignored)"
			sleep "$interval" &
			wait $! || true
		done
	) >>"$log_file" 2>&1 </dev/null &
	local sampler_pid=$!
	echo "$sampler_pid" >"$pid_file"
	log_info "Memory sampler started (PID $sampler_pid, interval ${interval}s, log: $log_file)"
}

cmd_stop() {
	local log_file="${1:?Log file is required}"
	local pid_file="${2:?PID file is required}"

	if [[ ! -f "$pid_file" ]]; then
		log_warning "No sampler PID file at $pid_file; nothing to stop"
		return 0
	fi

	local sampler_pid
	sampler_pid="$(cat "$pid_file")"
	if [[ -n "$sampler_pid" ]] && kill -0 "$sampler_pid" 2>/dev/null; then
		kill -TERM "$sampler_pid" 2>/dev/null || true
		# The sampler PID belongs to an earlier step's shell, so `wait` cannot
		# reap it here; poll briefly instead, then escalate to SIGKILL.
		for _ in $(seq 1 50); do
			kill -0 "$sampler_pid" 2>/dev/null || break
			sleep 0.1
		done
		if kill -0 "$sampler_pid" 2>/dev/null; then
			log_warning "Sampler PID $sampler_pid ignored SIGTERM; sending SIGKILL"
			kill -9 "$sampler_pid" 2>/dev/null || true
		fi
		log_info "Memory sampler stopped (PID $sampler_pid)"
	else
		log_warning "Sampler PID ${sampler_pid:-unknown} is not running; cleaning up"
	fi
	rm -f "$pid_file"

	# Capture the end state (peak memory right after the build finished) before
	# the stop marker so the uploaded log brackets the whole compile.
	{
		snapshot || echo "final snapshot failed (ignored)"
		echo "=== sampler stopped $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
	} >>"$log_file"
}

case "${1:-}" in
snapshot)
	snapshot
	;;
start)
	shift
	cmd_start "$@"
	;;
stop)
	shift
	cmd_stop "$@"
	;;
*)
	echo "Error: unknown command '${1:-}'" >&2
	echo "Usage: memory-sampler.sh <snapshot|start|stop> [arguments]" >&2
	exit 1
	;;
esac
