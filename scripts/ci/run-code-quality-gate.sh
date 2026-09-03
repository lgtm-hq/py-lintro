#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Evaluate docker-ci upstream jobs and assert the required code-quality gate.
#
# Required environment variables:
#   DOCKER_BUILD_RESULT, PRIMARY_LINT_RESULT
#
# Optional environment variables:
#   RETRY_LINT_RESULT
#   PRIMARY_LINT_STATUS, PRIMARY_LINT_EXIT_CODE, PRIMARY_LINT_CONCLUSION
#   RETRY_LINT_STATUS, RETRY_LINT_EXIT_CODE, RETRY_LINT_CONCLUSION
#   PRIMARY_LINT_TIMEOUT_FLAKE, PRIMARY_LINT_TIMED_OUT_TOOLS
#   RETRY_LINT_TIMEOUT_FLAKE, RETRY_LINT_TIMED_OUT_TOOLS

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Evaluate docker-ci upstream jobs and assert the required code-quality gate.

Usage:
  DOCKER_BUILD_RESULT=success \
    PRIMARY_LINT_RESULT=success scripts/ci/run-code-quality-gate.sh

Writes result, passed, status, exit-code, and infra-flake to GITHUB_OUTPUT
when set. infra-flake=true means the gate passed by absorbing runner noise
rather than by observing a successful lint run — including a tool-execution
timeout in the authoritative lint attempt (#1653), which is absorbed only on
that attempt's own zero-findings report.
EOF
	exit 0
fi

write_job_outputs() {
	local result="$1"
	local passed="$2"
	local status="$3"
	local exit_code="$4"
	local infra_flake="${5:-false}"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		{
			echo "result=${result}"
			echo "passed=${passed}"
			echo "status=${status}"
			echo "exit-code=${exit_code}"
			echo "infra-flake=${infra_flake}"
		} >>"${GITHUB_OUTPUT}"
	fi
}

read_assert_output() {
	local key="$1"
	local default="$2"
	local line
	line="$(grep -E "^${key}=" "${ASSERT_OUTPUT}" | tail -1 || true)"
	if [[ -z "${line}" ]]; then
		printf '%s' "${default}"
		return 0
	fi
	printf '%s' "${line#*=}"
}

EVALUATE_OUTPUT="${RUN_CODE_QUALITY_GATE_EVAL_OUTPUT:-$(mktemp "${TMPDIR:-/tmp}/code-quality-gate-eval.XXXXXX")}"
ASSERT_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/code-quality-gate-assert.XXXXXX")"

trap 'rm -f "${EVALUATE_OUTPUT}" "${ASSERT_OUTPUT}"' EXIT

: "${DOCKER_BUILD_RESULT:?}"
: "${PRIMARY_LINT_RESULT:?}"

GITHUB_OUTPUT="${EVALUATE_OUTPUT}" bash "${SCRIPT_DIR}/evaluate-code-quality-gate.sh"

upstream_result="$(grep -E '^upstream-result=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
status_output="$(grep -E '^status-output=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
exit_code_output="$(grep -E '^exit-code-output=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
upstream_conclusion="$(grep -E '^upstream-conclusion=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
verdict_source="$(grep -E '^verdict-source=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
timeout_flake="$(grep -E '^timeout-flake-output=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
timed_out_tools="$(grep -E '^timed-out-tools-output=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"

# The authoritative attempt's own tool-execution timeout verdict is lint-only
# evidence (#1653, lgtm-ci#746): an upstream docker-build failure is normalized
# to failed/1 here too, so it would be indistinguishable from a lint verdict.
# Scope it by verdict-source and drop it for anything that is not a lint
# verdict, so a build failure can never be absorbed as a lint timeout.
if [[ "${verdict_source}" != "lint" ]]; then
	timeout_flake=false
	timed_out_tools=""
fi

if UPSTREAM_RESULT="${upstream_result}" \
	STATUS_OUTPUT="${status_output}" \
	STATUS_EXPECTED=passed \
	EXIT_CODE_OUTPUT="${exit_code_output}" \
	UPSTREAM_CONCLUSION="${upstream_conclusion}" \
	TIMEOUT_FLAKE="${timeout_flake}" \
	TIMED_OUT_TOOLS="${timed_out_tools}" \
	GITHUB_OUTPUT="${ASSERT_OUTPUT}" \
	bash "${SCRIPT_DIR}/assert-required-check.sh"; then
	# infra-flake=true means the required check is green without a lint
	# verdict. The check stays green (that is the point of #1313) but
	# consumers that must not ship unlinted artefacts read this flag.
	write_job_outputs success true passed 0 "$(read_assert_output infra-flake false)"
	exit 0
fi

write_job_outputs failure false failed 1 false
exit 1
