#!/usr/bin/env bash
set -euo pipefail

# compile-semgrep-lock.sh - Re-resolve requirements-semgrep.txt from the
# committed .in pin with hashes. Renovate runs this after bumping the .in
# file so transitives are never edited in place.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IN_FILE="$PROJECT_ROOT/requirements-semgrep.in"

if [ ! -f "$IN_FILE" ]; then
	echo "Error: $IN_FILE not found" >&2
	exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
	echo "Error: uv is required to compile the isolated semgrep lockfile" >&2
	exit 1
fi

cd "$PROJECT_ROOT"
uv pip compile \
	--no-config \
	--generate-hashes \
	--python-version 3.11 \
	--output-file requirements-semgrep.txt \
	requirements-semgrep.in
