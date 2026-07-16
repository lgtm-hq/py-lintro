#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Verify a built lintro binary responds to --version and --help.

set -euo pipefail

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
	echo "Binary not found: $BINARY" >&2
	exit 1
fi

ls -lh "$(dirname "$BINARY")"
"$BINARY" --version
"$BINARY" --help | head -20 || echo "Help output truncated"
