#!/usr/bin/env bash
set -euo pipefail

# compile-semgrep-lock.sh - Re-resolve requirements-semgrep.txt from the
# committed .in pin with hashes.
#
# Run this by hand (or from an agent) whenever requirements-semgrep.in
# changes, and commit the result; transitives are never edited in place.
# Nothing regenerates the lockfile automatically — the Mend-hosted Renovate
# app never executes post-upgrade commands (#2436) — so CI enforces it
# instead: scripts/ci/check-semgrep-lock.sh fails the "Semgrep Lockfile
# Drift" check when the committed lockfile no longer matches the .in pin.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Re-resolve requirements-semgrep.txt from requirements-semgrep.in.

Usage:
  scripts/ci/compile-semgrep-lock.sh

Rewrites the committed hash-pinned lockfile (Python 3.11 floor, matching
requires-python). Commit the result together with the .in change; on a PR
whose lockfile drifted, CI's scripts/ci/check-semgrep-lock.sh gate turns the
"Semgrep Lockfile Drift" check red and the image publish will not run.

Requires uv on PATH.
EOF
	exit 0
fi

# shellcheck source=./semgrep-lock-lib.sh disable=SC1091 # sibling helper; resolved from SCRIPT_DIR at runtime
source "${SCRIPT_DIR}/semgrep-lock-lib.sh"

semgrep_lock_require_inputs
semgrep_lock_compile requirements-semgrep.txt
