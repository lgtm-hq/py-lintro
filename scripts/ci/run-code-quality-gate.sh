#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Evaluate docker-ci upstream jobs and assert the required code-quality gate.
#
# Fail-closed since #2296: when no lint attempt produced a verdict the gate
# writes result=failure, passed=false, status=no-verdict, infra-flake=true and
# exits 1, so the required lintro-code-quality check goes red and
# auto-rerun-on-infra-failure.yml retries the run.
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
when set. infra-flake=true means the failure (or the absorbed job failure)
was runner noise rather than a lint violation. Since #2296 an attempt that
produced no lint verdict — runner loss, cancellation, SIGTERM exit 143, or a
tool-execution timeout (#1653) — fails closed: result=failure, passed=false,
status=no-verdict, infra-flake=true. Only a post-lint job failure on top of a
passing lint verdict still greens the gate with infra-flake=true.
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
	# A green gate always rests on a lint verdict since #2296. infra-flake
	# is still true when the surrounding job failed after lint passed, and
	# consumers that must not ship unlinted artefacts read that flag.
	write_job_outputs success true passed 0 "$(read_assert_output infra-flake false)"
	exit 0
fi

# Carry the assert step's verdict through instead of hard-coding 'failed': a
# no-verdict failure (runner loss) must stay distinguishable from a genuine
# lint failure so the summary and the auto-rerun bot can tell them apart.
write_job_outputs \
	failure \
	false \
	"$(read_assert_output status failed)" \
	1 \
	"$(read_assert_output infra-flake false)"
exit 1
