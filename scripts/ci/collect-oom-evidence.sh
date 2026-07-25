#!/usr/bin/env bash
# collect-oom-evidence.sh
# Best-effort OOM-killer evidence collection after a failed build (#1707).
#
# Greps the kernel ring buffer (dmesg) for OOM-killer signatures, falling back
# to the systemd journal when dmesg is unavailable or restricted. dmesg is
# commonly locked down on hosted runners (kernel.dmesg_restrict), so every
# probe is guarded: this script ALWAYS exits 0 and notes any restriction in
# the output file instead of failing the workflow.

set -uo pipefail
# NOTE: deliberately no `set -e` — evidence collection must never fail the job.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Best-effort OOM-killer evidence collection (#1707).

Usage: collect-oom-evidence.sh <output-file>

Arguments:
  output-file  File the evidence report is written to.

The script greps dmesg (with a journalctl -k fallback) for OOM-killer
signatures. Every probe is guarded: restrictions (e.g. kernel.dmesg_restrict
on hosted runners) are noted in the output file and the script always exits 0.

Examples:
  collect-oom-evidence.sh oom-evidence.txt
EOF
	exit 0
fi

OUTPUT_FILE="${1:?Output file is required}"

# OOM-killer log signatures: the kill decision, the victim report, and the
# generic "out of memory" phrasing across kernel versions.
OOM_PATTERN='Out of memory|out of memory|oom-kill|oom_reap|Killed process|memory cgroup out of memory'

# Wall-clock bound for the macOS unified-log query. This step runs on the
# failure path of a job whose timeout the compile has mostly consumed, so a
# slow logging subsystem must not eat the remaining budget (Greptile #1707).
LOG_SHOW_TIMEOUT="${OOM_LOG_SHOW_TIMEOUT:-20}"

# run_bounded <seconds> <out-file> <command...> — run a command, SIGKILLing
# it after <seconds> so a hung probe cannot stall the failure-path job.
# Output goes to a regular file rather than a command-substitution pipe so a
# killed command's orphaned children cannot hold the reader open.
run_bounded() {
	local seconds="$1"
	local out_file="$2"
	shift 2
	"$@" >"$out_file" 2>&1 &
	local cmd_pid=$!
	(
		sleep "$seconds"
		kill -9 "$cmd_pid" 2>/dev/null
	) &
	local watchdog_pid=$!
	wait "$cmd_pid" 2>/dev/null
	local rc=$?
	# Disarm the watchdog when the command finished inside the bound.
	kill "$watchdog_pid" 2>/dev/null || true
	wait "$watchdog_pid" 2>/dev/null || true
	return "$rc"
}

{
	echo "=== OOM evidence collected $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="

	case "$(uname -s)" in
	Linux)
		if command -v dmesg &>/dev/null; then
			DMESG_OUT="$(dmesg 2>&1)"
			DMESG_RC=$?
			if [[ $DMESG_RC -eq 0 ]]; then
				echo "--- dmesg OOM matches ---"
				echo "$DMESG_OUT" | grep -iE "$OOM_PATTERN" ||
					echo "(no OOM-killer signatures found in dmesg)"
			else
				echo "--- dmesg restricted or failed (rc=$DMESG_RC); output follows ---"
				echo "$DMESG_OUT"
			fi
		else
			echo "--- dmesg not available on PATH ---"
			DMESG_RC=127
		fi

		if [[ "${DMESG_RC:-127}" -ne 0 ]] && command -v journalctl &>/dev/null; then
			echo "--- journalctl -k fallback OOM matches ---"
			JOURNAL_OUT="$(journalctl -k --no-pager 2>&1)"
			JOURNAL_RC=$?
			if [[ $JOURNAL_RC -ne 0 ]]; then
				# A failed probe is not a clean scan: report it instead of
				# claiming no signatures (CodeRabbit on #1707).
				echo "(journalctl failed, rc=$JOURNAL_RC; output follows)"
				echo "$JOURNAL_OUT"
			else
				echo "$JOURNAL_OUT" | grep -iE "$OOM_PATTERN" ||
					echo "(no OOM-killer signatures found in the kernel journal)"
			fi
		elif [[ "${DMESG_RC:-127}" -ne 0 ]]; then
			echo "--- journalctl not available; no kernel-log fallback ---"
		fi
		;;
	Darwin)
		# macOS has no dmesg OOM-killer log; Jetsam (memorystatus) events are
		# the equivalent and surface via the unified log.
		echo "--- dmesg OOM probe not applicable on macOS; checking Jetsam events ---"
		if command -v log &>/dev/null; then
			LOG_CAPTURE="$(mktemp)"
			run_bounded "$LOG_SHOW_TIMEOUT" "$LOG_CAPTURE" \
				log show --last 30m --predicate 'eventMessage CONTAINS "memorystatus"'
			LOG_RC=$?
			if [[ $LOG_RC -eq 137 ]]; then
				echo "(log show timed out after ${LOG_SHOW_TIMEOUT}s; skipped)"
			elif [[ $LOG_RC -ne 0 ]]; then
				# A failed probe is not a clean scan (CodeRabbit on #1707).
				echo "(log show failed, rc=$LOG_RC; output follows)"
				cat "$LOG_CAPTURE"
			else
				grep -i "memorystatus" "$LOG_CAPTURE" ||
					echo "(no Jetsam/memorystatus events found in the last 30m)"
			fi
			rm -f "$LOG_CAPTURE"
		else
			echo "--- log(1) not available; no macOS fallback ---"
		fi
		;;
	*)
		echo "--- unsupported platform: $(uname -s); no OOM evidence collected ---"
		;;
	esac
} >"$OUTPUT_FILE" 2>&1

echo "OOM evidence written to $OUTPUT_FILE (best-effort; restrictions noted in-file)"
exit 0
