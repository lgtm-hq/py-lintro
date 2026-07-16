#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# check-vuln-suppressions.sh — Verbose wrapper for lgtm-ci suppression check.
#
# Local override for py-lintro vuln-suppression-check (#1314). Adds trace
# logging and explicit failure context around the tooling script.
#
# Usage:
#   check-vuln-suppressions.sh
#
# Environment:
#   GH_TOKEN      GitHub token for PR creation (required by tooling script)
#   CONFIG_PATH   Suppression TOML path (default: .osv-scanner.toml)
#   VERBOSE       Set to 1 to enable bash trace (set -x); auto-enabled in Actions

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: check-vuln-suppressions.sh

Detect stale or expired vulnerability suppressions in .osv-scanner.toml.

Delegates to lgtm-ci check-vuln-suppressions.sh with verbose diagnostics.
EOF
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
TOOLING_SCRIPT="${REPO_ROOT}/.lgtm-ci-tooling/scripts/ci/security/check-vuln-suppressions.sh"
TOOLING_LIB="${REPO_ROOT}/.lgtm-ci-tooling/scripts/ci/lib"

_log_info() {
	echo "[INFO] $*" >&2
}

_log_error() {
	echo "[ERROR] $*" >&2
}

if [[ -f "${TOOLING_LIB}/log.sh" ]]; then
	# shellcheck source=/dev/null
	source "${TOOLING_LIB}/log.sh"
else
	log_info() { _log_info "$@"; }
	log_error() { _log_error "$@"; }
fi

if [[ "${GITHUB_ACTIONS:-}" == "true" || "${VERBOSE:-}" == "1" ]]; then
	set -x
fi

log_info "Vulnerability suppression check starting"
log_info "Repository root: ${REPO_ROOT}"
log_info "CONFIG_PATH=${CONFIG_PATH:-.osv-scanner.toml}"
log_info "WORKFLOW_FILE=${WORKFLOW_FILE:-<unset>}"
log_info "Tooling script: ${TOOLING_SCRIPT}"

if [[ ! -f "$TOOLING_SCRIPT" ]]; then
	log_error "Missing lgtm-ci tooling script: ${TOOLING_SCRIPT}"
	log_error "Ensure checkout-and-harden runs before this step."
	exit 1
fi

set +e
bash "$TOOLING_SCRIPT"
exit_code=$?
set -e

if [[ "$exit_code" -ne 0 ]]; then
	log_error "check-vuln-suppressions failed with exit code ${exit_code}"
	log_error "Review osv-scanner probe output and suppression config above."
	exit "$exit_code"
fi
