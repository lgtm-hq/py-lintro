#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Verify a built lintro binary before it is packaged: --version,
# the interactive AI review under a pty, an MCP start-and-exit, and --help.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 1 ]]; then
	cat <<'EOF'
Verify a built lintro binary before packaging.

Usage: verify_built_binary.sh <binary-path>

Runs --version (required), the interactive AI review through a pty
(required), an MCP server start-and-exit (required) and --help
(non-fatal truncation) checks.
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

# MCP server start-and-exit: the stdio server is the one entry point the smoke
# test never reaches. With stdin at EOF a working server exits 0 immediately.
# Release binaries are built without the optional `lintro[mcp]` extra, so the
# documented UsageError is the other accepted outcome; a traceback, any other
# non-zero exit, or a server that never exits is a packaging failure.
#
# `click.UsageError.exit_code`, pinned against click in
# tests/scripts/test_release_gate_contracts.py: only that code may carry the
# missing-extra message, so a crash whose traceback happens to quote it, or a
# binary that prints it and exits 0 without serving, still fails.
MCP_USAGE_ERROR_EXIT=2

# Whole-probe budget. A hung server would otherwise burn the runner until the
# job timeout. `timeout(1)` is GNU coreutils and absent from the macOS
# runners, so the wait is a poll loop; the override exists for the bats suite.
MCP_BUDGET_SECONDS="${LINTRO_VERIFY_MCP_BUDGET_SECONDS:-60}"

"$BINARY" mcp </dev/null >"$MCP_OUTPUT" 2>&1 &
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
	log_error "MCP server did not exit within ${MCP_BUDGET_SECONDS}s of EOF:"
	cat "$MCP_OUTPUT"
	exit 1
fi

if [[ "$MCP_STATUS" -eq 0 ]]; then
	# Exit 0 is the success signal on its own: the stdio server prints no
	# start banner, and inventing one to grep for would be a new contract
	# maintained only for this gate.
	log_success "MCP server started and exited at EOF"
elif [[ "$MCP_STATUS" -eq "$MCP_USAGE_ERROR_EXIT" ]] &&
	grep -q "requires lintro\[mcp\]" "$MCP_OUTPUT"; then
	log_info "MCP server: optional SDK not bundled (documented UsageError)"
else
	log_error "MCP server start-and-exit failed (exit ${MCP_STATUS}):"
	cat "$MCP_OUTPUT"
	exit 1
fi

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
