#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Evaluate docker-ci upstream jobs for the required lintro-code-quality gate.
#
# Selects the effective dogfooding lint attempt (retry when it ran) and writes
# normalized upstream values for assert-required-check.sh.
#
# Required environment variables:
#   DOCKER_BUILD_RESULT
#   MANIFEST_SYNC_RESULT
#   PRIMARY_LINT_RESULT
#
# Optional environment variables:
#   RETRY_LINT_RESULT
#   PRIMARY_LINT_STATUS, PRIMARY_LINT_EXIT_CODE, PRIMARY_LINT_CONCLUSION
#   RETRY_LINT_STATUS, RETRY_LINT_EXIT_CODE, RETRY_LINT_CONCLUSION
#   PRIMARY_FAILURE_REASON, RETRY_FAILURE_REASON

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Evaluate docker-ci upstream jobs for the required lintro-code-quality gate.

Usage:
  DOCKER_BUILD_RESULT=success MANIFEST_SYNC_RESULT=success \
    PRIMARY_LINT_RESULT=success scripts/ci/evaluate-code-quality-gate.sh

Writes upstream-result, status-output, exit-code-output, upstream-conclusion,
and failure-reason to GITHUB_OUTPUT when set.
EOF
	exit 0
fi

: "${DOCKER_BUILD_RESULT:?}"
: "${MANIFEST_SYNC_RESULT:?}"
: "${PRIMARY_LINT_RESULT:?}"

write_output() {
	local key="$1"
	local value="$2"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		echo "${key}=${value}" >>"${GITHUB_OUTPUT}"
	fi
}

if [[ "${DOCKER_BUILD_RESULT}" != "success" ]]; then
	write_output upstream-result "${DOCKER_BUILD_RESULT}"
	write_output status-output "failed"
	write_output exit-code-output "1"
	write_output upstream-conclusion "${DOCKER_BUILD_RESULT}"
	write_output failure-reason "docker-build ${DOCKER_BUILD_RESULT}"
	exit 0
fi

if [[ "${MANIFEST_SYNC_RESULT}" != "success" && "${MANIFEST_SYNC_RESULT}" != "skipped" ]]; then
	write_output upstream-result "${MANIFEST_SYNC_RESULT}"
	write_output status-output "failed"
	write_output exit-code-output "1"
	write_output upstream-conclusion "${MANIFEST_SYNC_RESULT}"
	write_output failure-reason "manifest-sync ${MANIFEST_SYNC_RESULT}"
	exit 0
fi

effective_result="${PRIMARY_LINT_RESULT}"
effective_status="${PRIMARY_LINT_STATUS:-}"
effective_exit_code="${PRIMARY_LINT_EXIT_CODE:-}"
effective_conclusion="${PRIMARY_LINT_CONCLUSION:-}"
effective_failure_reason="${PRIMARY_FAILURE_REASON:-}"

if [[ "${RETRY_LINT_RESULT:-}" == "success" ]]; then
	effective_result="${RETRY_LINT_RESULT}"
	effective_status="${RETRY_LINT_STATUS:-}"
	effective_exit_code="${RETRY_LINT_EXIT_CODE:-}"
	effective_conclusion="${RETRY_LINT_CONCLUSION:-}"
	effective_failure_reason="${RETRY_FAILURE_REASON:-}"
fi

if [[ "${effective_result}" == "success" ]]; then
	write_output upstream-result success
	write_output status-output "${effective_status:-passed}"
	write_output exit-code-output "${effective_exit_code:-0}"
	write_output upstream-conclusion success
	write_output failure-reason ""
	exit 0
fi

write_output upstream-result "${effective_result}"
write_output status-output "${effective_status}"
write_output exit-code-output "${effective_exit_code}"
write_output upstream-conclusion "${effective_conclusion}"
write_output failure-reason "${effective_failure_reason}"
