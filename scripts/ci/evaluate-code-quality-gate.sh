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

# GITHUB_OUTPUT is a line-oriented key=value file, so a newline inside a value
# would be parsed as a new record. FAILURE_REASON is free text (a step log
# snippet once #1655 wires it), and a value such as
# $'boom\nstatus-output=passed' would otherwise forge a passing verdict. Refuse
# to write instead of emitting a malformed record — the caller runs under
# `set -e`, so the gate fails closed (red) rather than green.
write_output() {
	local key="$1"
	local value="$2"
	if [[ "${value}" == *$'\n'* || "${value}" == *$'\r'* ]]; then
		echo "::error::${key} must not contain a newline or carriage return" >&2
		exit 1
	fi
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		printf '%s=%s\n' "${key}" "${value}" >>"${GITHUB_OUTPUT}"
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

# A genuine lint verdict is status=failed or exit-code=1 (lintro's only
# non-zero lint exit). Runner kills surface as 143, cancellations/timeouts as
# empty outputs — neither is a lint verdict.
reports_genuine_lint_failure() {
	[[ "${1}" == "failed" || "${2}" == "1" ]]
}

effective_result="${PRIMARY_LINT_RESULT}"
effective_status="${PRIMARY_LINT_STATUS:-}"
effective_exit_code="${PRIMARY_LINT_EXIT_CODE:-}"
effective_conclusion="${PRIMARY_LINT_CONCLUSION:-}"
effective_failure_reason="${PRIMARY_FAILURE_REASON:-}"

# The retry (full-run only) exists to give a genuinely flaked primary a second
# chance, so it becomes authoritative only when it is itself authoritative:
#   - it passed (the tree is clean; a real violation is deterministic and would
#     have failed the retry too), or
#   - it reported its own genuine lint failure.
# It must NOT override the primary when the retry itself flaked (killed at 143,
# cancelled, empty outputs): otherwise a primary that reported failed/1 would be
# replaced by an absorbable retry result and the real failure would be masked
# (Greptile P1 on #1650). When the primary already flaked too, we prefer the
# retry as the later of two non-verdicts (both get absorbed, infra-flake=true).
if [[ "${RETRY_LINT_RESULT:-}" == "success" || "${RETRY_LINT_RESULT:-}" == "failure" ]]; then
	if [[ "${RETRY_LINT_RESULT}" == "success" ]] ||
		reports_genuine_lint_failure "${RETRY_LINT_STATUS:-}" "${RETRY_LINT_EXIT_CODE:-}" ||
		! reports_genuine_lint_failure "${PRIMARY_LINT_STATUS:-}" "${PRIMARY_LINT_EXIT_CODE:-}"; then
		effective_result="${RETRY_LINT_RESULT}"
		effective_status="${RETRY_LINT_STATUS:-}"
		effective_exit_code="${RETRY_LINT_EXIT_CODE:-}"
		effective_conclusion="${RETRY_LINT_CONCLUSION:-}"
		effective_failure_reason="${RETRY_FAILURE_REASON:-}"
	fi
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
