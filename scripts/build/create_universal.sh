#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Create a macOS universal binary from arm64 and x86_64 builds.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 3 ]]; then
	cat <<'EOF'
Create a macOS universal lintro binary with lipo.

Usage: create_universal.sh <arm64-binary> <x86_64-binary> <output-path>

Combines the two architecture-specific binaries, marks the result executable,
and prints file(1) and ls verification output.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

ARM64_BINARY="$1"
X86_64_BINARY="$2"
OUTPUT="$3"

for binary in "$ARM64_BINARY" "$X86_64_BINARY"; do
	if [[ ! -f "$binary" ]]; then
		log_error "Input binary not found: $binary"
		exit 1
	fi
done

if ! command -v lipo >/dev/null 2>&1; then
	log_error "lipo not found (macOS only)"
	exit 1
fi

OUTPUT_DIR="$(dirname "$OUTPUT")"
mkdir -p "$OUTPUT_DIR"

lipo -create "$ARM64_BINARY" "$X86_64_BINARY" -output "$OUTPUT"
chmod +x "$OUTPUT"

file "$OUTPUT"
ls -lh "$OUTPUT"
