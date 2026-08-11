#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Verify a built lintro binary responds to --version and --help.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 1 ]]; then
	cat <<'EOF'
Verify a built lintro binary before packaging.

Usage: verify_built_binary.sh <binary-path>

Runs --version (required) and --help (non-fatal truncation) checks.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

BINARY="$1"

if [[ ! -f "$BINARY" ]]; then
	log_error "Binary not found: $BINARY"
	exit 1
fi

ls -lh "$(dirname "$BINARY")"

# --version is the build gate: a binary that cannot report its version is not
# a valid build.
"$BINARY" --version

# --help stays non-fatal (it is diagnostic output only), but capture it before
# truncating: piping straight into `head` under `set -o pipefail` turns head's
# SIGPIPE into a failure that is indistinguishable from a genuinely broken
# --help, and the old `|| echo "Help output truncated"` swallowed both.
HELP_OUTPUT="$(mktemp)"
trap 'rm -f "$HELP_OUTPUT"' EXIT
if "$BINARY" --help >"$HELP_OUTPUT" 2>&1; then
	head -20 "$HELP_OUTPUT"
else
	log_warning "--help exited non-zero (non-fatal); first 20 lines follow"
	head -20 "$HELP_OUTPUT"
fi
