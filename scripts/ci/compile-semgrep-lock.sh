#!/usr/bin/env bash
set -euo pipefail

# compile-semgrep-lock.sh - Re-resolve requirements-semgrep.txt from the
# committed .in pin with hashes.
#
# Run this by hand (or from an agent) whenever requirements-semgrep.in
# changes, and commit the result; transitives are never edited in place.
# The semgrep-lock-renovate workflow recompiles on Renovate pushes (#2841);
# the Mend-hosted app itself never executes post-upgrade commands (#2436).
# CI still enforces the result: scripts/ci/check-semgrep-lock.sh fails the
# "Semgrep Lockfile Drift" check when the committed lockfile no longer matches the .in pin.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Re-resolve requirements-semgrep.txt from requirements-semgrep.in.

Usage:
  scripts/ci/compile-semgrep-lock.sh [--upgrade]

Rewrites the committed hash-pinned lockfile (Python 3.11 floor, matching
requires-python). Commit the result together with the .in change; on a PR
whose lockfile drifted, CI's scripts/ci/check-semgrep-lock.sh gate turns the
"Semgrep Lockfile Drift" check red and the image publish will not run.

Use --upgrade to refresh all transitive dependencies instead of preserving
existing compatible pins. Renovate lock-file-maintenance uses this mode.
When GITHUB_OUTPUT is set, writes changed=true/false for the lockfile diff.

Requires uv on PATH.
EOF
	exit 0
fi

if [[ $# -gt 1 || ($# -eq 1 && "$1" != "--upgrade") ]]; then
	echo "Error: expected no arguments or --upgrade (see --help)" >&2
	exit 2
fi

# shellcheck source=./semgrep-lock-lib.sh disable=SC1091 # sibling helper; resolved from SCRIPT_DIR at runtime
source "${SCRIPT_DIR}/semgrep-lock-lib.sh"

semgrep_lock_require_inputs
semgrep_lock_compile requirements-semgrep.txt "$@"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	PROJECT_ROOT="$(semgrep_lock_project_root)"
	status=0
	git -C "${PROJECT_ROOT}" diff --quiet -- requirements-semgrep.txt || status=$?
	case "$status" in
	0) echo 'changed=false' >>"${GITHUB_OUTPUT}" ;;
	1) echo 'changed=true' >>"${GITHUB_OUTPUT}" ;;
	*) exit "$status" ;;
	esac
fi
