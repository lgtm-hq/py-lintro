#!/usr/bin/env bash
set -euo pipefail

# check-semgrep-lock.sh - Fail when requirements-semgrep.txt no longer
# matches requirements-semgrep.in (#2436).
#
# Nothing recompiles the isolated semgrep lockfile automatically: the
# Mend-hosted Renovate app does not execute post-upgrade commands, so a
# semgrep bump used to ship a stale lockfile and turn every downstream job
# red for unrelated-looking reasons. This gate resolves the .in pin into a
# temporary file through the same helper compile-semgrep-lock.sh uses, diffs
# it against the committed lockfile, and names the one command that fixes it.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Fail when the committed semgrep lockfile drifted from its .in pin.

Usage:
  scripts/ci/check-semgrep-lock.sh

Re-resolves requirements-semgrep.in into a temporary file with the same
invocation as scripts/ci/compile-semgrep-lock.sh and compares it with the
committed requirements-semgrep.txt, ignoring uv's generated header comments
(they name the output path, which differs by construction).

Exit codes:
  0  the committed lockfile is up to date
  1  drift (the diff and the recompile command are printed), or a missing
     input (requirements-semgrep.in, requirements-semgrep.txt, uv)

Requires uv on PATH.
EOF
	exit 0
fi

# shellcheck source=scripts/ci/semgrep-lock-lib.sh
source "${SCRIPT_DIR}/semgrep-lock-lib.sh"

semgrep_lock_require_inputs

PROJECT_ROOT="$(semgrep_lock_project_root)"
COMMITTED_LOCK="${PROJECT_ROOT}/requirements-semgrep.txt"

if [[ ! -f "${COMMITTED_LOCK}" ]]; then
	echo "Error: ${COMMITTED_LOCK} not found" >&2
	echo "Run: scripts/ci/compile-semgrep-lock.sh" >&2
	exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/semgrep-lock-check.XXXXXX")"
trap 'rm -rf "${WORK_DIR}"' EXIT

FRESH_LOCK="${WORK_DIR}/requirements-semgrep.txt"

# Seed the temporary output with the committed lockfile. uv reads an existing
# output file as resolution preferences, so compile-semgrep-lock.sh (which
# rewrites the lockfile in place) only moves pins the .in change forces. The
# gate has to resolve under the same preferences, or an unrelated PR would go
# red the moment any transitive published a newer release upstream.
cp "${COMMITTED_LOCK}" "${FRESH_LOCK}"

echo "Re-resolving requirements-semgrep.in to check the committed lockfile..."
semgrep_lock_compile "${FRESH_LOCK}"

semgrep_lock_strip_header "${COMMITTED_LOCK}" >"${WORK_DIR}/committed.body"
semgrep_lock_strip_header "${FRESH_LOCK}" >"${WORK_DIR}/fresh.body"

if diff -u \
	--label 'requirements-semgrep.txt (committed)' \
	--label 'requirements-semgrep.txt (resolved from requirements-semgrep.in)' \
	"${WORK_DIR}/committed.body" \
	"${WORK_DIR}/fresh.body" >"${WORK_DIR}/drift.diff"; then
	echo "requirements-semgrep.txt is up to date with requirements-semgrep.in"
	exit 0
fi

echo "::error::requirements-semgrep.txt is out of date with requirements-semgrep.in" >&2
cat "${WORK_DIR}/drift.diff" >&2
echo "Run: scripts/ci/compile-semgrep-lock.sh" >&2
exit 1
