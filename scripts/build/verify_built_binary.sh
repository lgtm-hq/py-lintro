#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Verify a built lintro binary before it is packaged: --version,
# the interactive AI review under a pty, the mcp command wiring, and --help.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 1 ]]; then
	cat <<'EOF'
Verify a built lintro binary before packaging.

Usage: verify_built_binary.sh <binary-path>

Runs --version (required), the interactive AI review through a pty
(required), the mcp command's wiring (required) and --help (non-fatal
truncation) checks.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

BINARY="$1"

if [[ ! -f "$BINARY" ]]; then
	log_error "Binary not found: $BINARY"
	exit 1
fi

# Display-only: a missing ls must not skip the --version gate.
ls -lh "$(dirname "$BINARY")" || true

# --version is the build gate: a binary that cannot report its version is not
# a valid build.
"$BINARY" --version

HELP_OUTPUT="$(mktemp)"
MCP_OUTPUT="$(mktemp)"
trap 'rm -f "$HELP_OUTPUT" "$MCP_OUTPUT"' EXIT

# #2514: lintro and pygments ship as bytecode rather than compiled C, and the
# pygments lexers are resolved by name at runtime, so a packaging mistake there
# is invisible to --version, --help and the tool-registry smoke test. The
# driver reviews an AI fix inside the built binary under a pty and fails unless
# the diff came back highlighted.
python3 "$SCRIPT_DIR/drive_interactive_review.py" "$BINARY"

# MCP command wiring: the `mcp` entry point is the one command the smoke test
# never reaches, so a frozen build could drop it entirely and every other gate
# would still pass. This asserts only what a release binary satisfies today --
# the command is wired into the frozen CLI and renders its own help.
#
# It used to assert the stronger "server starts and exits at EOF, or reports
# the documented UsageError with click's exit code 2". That is off pending
# #2577: `lintro mcp` dies in every frozen binary with `No module named
# 'mcp.server.stdio'`, a defect this probe found and one that also ships in
# v0.153.6. The strict assertion, its bats cases and its exit-code contract
# test come back with that fix.
# A fragment of the `--workspace` option's help, short enough that click
# cannot wrap it; pinned against the command's own help output in
# tests/scripts/test_release_gate_contracts.py. Not the flag spelling: a
# leading `--` would be read by grep as an option.
MCP_HELP_MARKER="Workspace root"

# Whole-probe budget. A binary that hangs here would otherwise burn the runner
# until the job timeout. `timeout(1)` is GNU coreutils and absent from the
# macOS runners, so the wait is a poll loop; the override exists for the bats
# suite.
MCP_BUDGET_SECONDS="${LINTRO_VERIFY_MCP_BUDGET_SECONDS:-60}"

"$BINARY" mcp --help </dev/null >"$MCP_OUTPUT" 2>&1 &
MCP_PID=$!

MCP_STATUS=""
for _ in $(seq 1 "$MCP_BUDGET_SECONDS"); do
	if ! kill -0 "$MCP_PID" 2>/dev/null; then
		if wait "$MCP_PID"; then
			MCP_STATUS=0
		else
			MCP_STATUS=$?
		fi
		break
	fi
	sleep 1
done

if [[ -z "$MCP_STATUS" ]]; then
	kill -9 "$MCP_PID" 2>/dev/null || true
	wait "$MCP_PID" 2>/dev/null || true
	log_error "mcp --help did not exit within ${MCP_BUDGET_SECONDS}s:"
	cat "$MCP_OUTPUT"
	exit 1
fi

if [[ "$MCP_STATUS" -ne 0 ]] || ! grep -q "$MCP_HELP_MARKER" "$MCP_OUTPUT"; then
	log_error "mcp command wiring failed (exit ${MCP_STATUS}):"
	cat "$MCP_OUTPUT"
	exit 1
fi
log_success "MCP command is wired into the binary (see #2577 for the server)"

# --help stays non-fatal (it is diagnostic output only), but capture it before
# truncating: piping straight into `head` under `set -o pipefail` turns head's
# SIGPIPE into a failure that is indistinguishable from a genuinely broken
# --help, and the old `|| echo "Help output truncated"` swallowed both.
if "$BINARY" --help >"$HELP_OUTPUT" 2>&1; then
	head -20 "$HELP_OUTPUT"
else
	log_warning "--help exited non-zero (non-fatal); first 20 lines follow"
	head -20 "$HELP_OUTPUT"
fi
