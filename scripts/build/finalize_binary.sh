#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Rename a built binary, ensure executable, compute SHA256, and write GitHub output.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 2 ]]; then
	cat <<'EOF'
Finalize a built lintro binary for CI artifact upload.

Usage: finalize_binary.sh <source-binary> <target-path> [label]

Moves the source binary to the target path, ensures it is executable,
computes SHA256, writes sha256 to GITHUB_OUTPUT when set, and prints a
verification line with file size and hash.

Arguments:
  source-binary  Path to the built binary before rename.
  target-path    Destination path (e.g. dist/nuitka/lintro-macos-arm64).
  label          Optional display label for log output (defaults to target basename).

Environment:
  GITHUB_OUTPUT  When set, appends sha256=<hash> for downstream workflow steps.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

SOURCE="$1"
TARGET="$2"
LABEL="${3:-$(basename "$TARGET")}"

if [[ ! -f "$SOURCE" ]]; then
	log_error "Source binary not found: $SOURCE"
	exit 1
fi

TARGET_DIR="$(dirname "$TARGET")"
mkdir -p "$TARGET_DIR"
mv "$SOURCE" "$TARGET"
chmod +x "$TARGET"

if command -v sha256sum >/dev/null 2>&1; then
	SHA="$(sha256sum "$TARGET" | cut -d' ' -f1)"
elif command -v shasum >/dev/null 2>&1; then
	SHA="$(shasum -a 256 "$TARGET" | cut -d' ' -f1)"
else
	log_error "No SHA256 tool found (expected sha256sum or shasum)"
	exit 1
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	echo "sha256=$SHA" >>"$GITHUB_OUTPUT"
fi

# Everything below is display-only: the rename, chmod, hash and GITHUB_OUTPUT
# write are already done. Under `set -e` a failure here would fail the step and
# skip the upload, so every command in this block falls back instead of aborting.
# BSD stat wants -f %z, GNU stat wants -c %s, and a stat missing both (or
# missing entirely) yields 0 rather than a non-zero exit.
SIZE_BYTES="$(stat -f %z "$TARGET" 2>/dev/null || stat -c %s "$TARGET" 2>/dev/null || echo 0)"
if command -v numfmt >/dev/null 2>&1; then
	# Restores the readable "11M" form the old `ls -lh` parsing gave us, without
	# depending on ls output columns. numfmt is GNU-only, hence the fallback.
	SIZE_DISPLAY="$(numfmt --to=iec "$SIZE_BYTES" 2>/dev/null || printf '%s bytes' "$SIZE_BYTES")"
else
	SIZE_DISPLAY="${SIZE_BYTES} bytes"
fi
log_info "SHA256 for ${LABEL}: $SHA"
log_success "Finalized ${TARGET}: size=${SIZE_DISPLAY} sha256=${SHA}"
