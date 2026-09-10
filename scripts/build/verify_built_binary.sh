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
# documented UsageError is the other accepted outcome; a traceback or any other
# non-zero exit is a packaging failure.
if "$BINARY" mcp </dev/null >"$MCP_OUTPUT" 2>&1; then
	log_success "MCP server started and exited at EOF"
elif grep -q "requires lintro\[mcp\]" "$MCP_OUTPUT"; then
	log_info "MCP server: optional SDK not bundled (documented UsageError)"
else
	log_error "MCP server start-and-exit failed:"
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
