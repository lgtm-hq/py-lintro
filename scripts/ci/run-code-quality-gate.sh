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
#   TIMEOUT_FLAKE - 'true' when the dogfood no-silent-skip gate proved the run
#                   failed only on tool-execution timeouts with zero findings

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Evaluate docker-ci upstream jobs and assert the required code-quality gate.

Usage:
  DOCKER_BUILD_RESULT=success MANIFEST_SYNC_RESULT=success \
    PRIMARY_LINT_RESULT=success scripts/ci/run-code-quality-gate.sh

Writes result, passed, status, exit-code, and infra-flake to GITHUB_OUTPUT
when set. infra-flake=true means the gate passed by absorbing runner noise
rather than by observing a successful lint run.

Set TIMEOUT_FLAKE=true (from the dogfood no-silent-skip gate) to absorb a
failure proven to be a tool-execution timeout with zero findings (#1653).
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
: "${MANIFEST_SYNC_RESULT:?}"
: "${PRIMARY_LINT_RESULT:?}"

GITHUB_OUTPUT="${EVALUATE_OUTPUT}" bash "${SCRIPT_DIR}/evaluate-code-quality-gate.sh"

upstream_result="$(grep -E '^upstream-result=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
status_output="$(grep -E '^status-output=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
exit_code_output="$(grep -E '^exit-code-output=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
upstream_conclusion="$(grep -E '^upstream-conclusion=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"
verdict_source="$(grep -E '^verdict-source=' "${EVALUATE_OUTPUT}" | tail -1 | cut -d= -f2-)"

# The tool-execution timeout proof (#1653) is evidence about the *lint* run
# only. A failed docker-build/manifest-sync also surfaces as failed/1 here, so
# forwarding the flag unconditionally would absorb an upstream build failure.
# Scope it to a lint verdict.
timeout_flake="${TIMEOUT_FLAKE:-}"
if [[ "${verdict_source}" != "lint" ]]; then
	timeout_flake=""
fi

if UPSTREAM_RESULT="${upstream_result}" \
	STATUS_OUTPUT="${status_output}" \
	STATUS_EXPECTED=passed \
	EXIT_CODE_OUTPUT="${exit_code_output}" \
	UPSTREAM_CONCLUSION="${upstream_conclusion}" \
	TIMEOUT_FLAKE="${timeout_flake}" \
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
