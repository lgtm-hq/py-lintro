#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Evaluate docker-ci upstream jobs and assert the required code-quality gate.
#
# Required environment variables:
#   DOCKER_BUILD_RESULT, MANIFEST_SYNC_RESULT, PRIMARY_LINT_RESULT
#
# Optional environment variables:
#   RETRY_LINT_RESULT
#   PRIMARY_LINT_STATUS, PRIMARY_LINT_EXIT_CODE, PRIMARY_LINT_CONCLUSION
#   RETRY_LINT_STATUS, RETRY_LINT_EXIT_CODE, RETRY_LINT_CONCLUSION
#   PRIMARY_FAILURE_REASON, RETRY_FAILURE_REASON

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVALUATE_OUTPUT="${RUN_CODE_QUALITY_GATE_EVAL_OUTPUT:-${TMPDIR:-/tmp}/code-quality-gate-eval.$$}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Evaluate docker-ci upstream jobs and assert the required code-quality gate.

Usage:
  DOCKER_BUILD_RESULT=success MANIFEST_SYNC_RESULT=success \
    PRIMARY_LINT_RESULT=success scripts/ci/run-code-quality-gate.sh

Writes result, passed, status, and exit-code to GITHUB_OUTPUT when set.
EOF
	exit 0
fi

write_job_outputs() {
	local result="$1"
	local passed="$2"
	local status="$3"
	local exit_code="$4"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		{
			echo "result=${result}"
			echo "passed=${passed}"
			echo "status=${status}"
			echo "exit-code=${exit_code}"
		} >>"${GITHUB_OUTPUT}"
	fi
}

trap 'rm -f "${EVALUATE_OUTPUT}"' EXIT

: "${DOCKER_BUILD_RESULT:?}"
: "${MANIFEST_SYNC_RESULT:?}"
: "${PRIMARY_LINT_RESULT:?}"

GITHUB_OUTPUT="${EVALUATE_OUTPUT}" bash "${SCRIPT_DIR}/evaluate-code-quality-gate.sh"

upstream_result="$(grep -E '^upstream-result=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
status_output="$(grep -E '^status-output=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
exit_code_output="$(grep -E '^exit-code-output=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
upstream_conclusion="$(grep -E '^upstream-conclusion=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
failure_reason="$(grep -E '^failure-reason=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"

if UPSTREAM_RESULT="${upstream_result}" \
	STATUS_OUTPUT="${status_output}" \
	STATUS_EXPECTED=passed \
	EXIT_CODE_OUTPUT="${exit_code_output}" \
	UPSTREAM_CONCLUSION="${upstream_conclusion}" \
	FAILURE_REASON="${failure_reason}" \
	bash "${SCRIPT_DIR}/assert-required-check.sh"; then
	write_job_outputs success true passed 0
	exit 0
fi

write_job_outputs failure false failed 1
exit 1
