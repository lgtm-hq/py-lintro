#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Classify upstream CI failures caused by runner infrastructure noise.
#
# Required environment variables:
#   UPSTREAM_RESULT - GitHub job result (success, failure, cancelled, skipped)
#
# Optional environment variables:
#   UPSTREAM_CONCLUSION - Job conclusion when distinct from result
#   STATUS_OUTPUT       - Upstream lint status output (passed, failed, or empty)
#   EXIT_CODE_OUTPUT    - Upstream lint exit code (0, 1, 143, or empty)
#   FAILURE_REASON      - Free-text step log snippet for flake signatures

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Classify upstream CI failures caused by runner infrastructure noise.

Usage:
  UPSTREAM_RESULT=failure STATUS_OUTPUT= EXIT_CODE_OUTPUT= \
    scripts/ci/is-infra-flake-failure.sh && echo infra || echo lint

Environment variables:
  UPSTREAM_RESULT       GitHub job result (required)
  UPSTREAM_CONCLUSION   Job conclusion when distinct from result
  STATUS_OUTPUT         Upstream lint status output
  EXIT_CODE_OUTPUT      Upstream lint exit code
  FAILURE_REASON        Free-text step log snippet
EOF
	exit 0
fi

: "${UPSTREAM_RESULT:?UPSTREAM_RESULT is required}"

is_infra_flake_failure() {
	local result="$1"
	local conclusion="${2:-}"
	local status_output="${3:-}"
	local exit_code_output="${4:-}"
	local failure_reason="${5:-}"

	if [[ "${result}" == "cancelled" || "${result}" == "timed_out" ]]; then
		return 0
	fi

	if [[ "${conclusion}" == "cancelled" || "${conclusion}" == "timed_out" ]]; then
		return 0
	fi

	if [[ "${exit_code_output}" == "143" ]]; then
		return 0
	fi

	if [[ "${result}" == "failure" && -z "${status_output}" && -z "${exit_code_output}" ]]; then
		return 0
	fi

	if [[ "${failure_reason}" == *"shutdown signal"* ]]; then
		return 0
	fi

	if [[ "${failure_reason}" == *"ETIMEDOUT"* || "${failure_reason}" == *"CreateArtifact"* ]]; then
		return 0
	fi

	return 1
}

if is_infra_flake_failure \
	"${UPSTREAM_RESULT}" \
	"${UPSTREAM_CONCLUSION:-}" \
	"${STATUS_OUTPUT:-}" \
	"${EXIT_CODE_OUTPUT:-}" \
	"${FAILURE_REASON:-}"; then
	exit 0
fi

exit 1
