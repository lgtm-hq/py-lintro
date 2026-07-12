#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Fail when an upstream reusable job did not pass required outputs.
# Treats runner infra-cancelled/timeout conclusions separately from lint failures.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Fail when an upstream reusable job did not pass required outputs.

Infra-cancelled/timeout upstream results are treated as non-blocking flakes.

Usage:
  UPSTREAM_RESULT=success scripts/ci/assert-required-check.sh

Environment variables:
  UPSTREAM_RESULT       Upstream job result (required)
  UPSTREAM_CONCLUSION   Upstream job conclusion (optional)
  PASSED_OUTPUT         When non-empty, must be the string true
  STATUS_OUTPUT         When non-empty, must equal STATUS_EXPECTED unless infra flake
  STATUS_EXPECTED       Expected STATUS_OUTPUT value (default: passed)
  EXIT_CODE_OUTPUT      Upstream lint exit code for infra flake detection
  FAILURE_REASON        Free-text step log snippet for infra flake detection
EOF
	exit 0
fi

write_gate_outputs() {
	local exit_code="$1"
	local status="$2"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		echo "exit-code=${exit_code}" >>"${GITHUB_OUTPUT}"
		echo "status=${status}" >>"${GITHUB_OUTPUT}"
	fi
}

if [[ ! ${UPSTREAM_RESULT+x} ]]; then
	echo "::error::UPSTREAM_RESULT not set"
	write_gate_outputs 1 failed
	exit 1
fi

STATUS_EXPECTED="${STATUS_EXPECTED:-passed}"

if [[ "${UPSTREAM_RESULT}" != "success" ]]; then
	if UPSTREAM_RESULT="${UPSTREAM_RESULT}" \
		UPSTREAM_CONCLUSION="${UPSTREAM_CONCLUSION:-}" \
		STATUS_OUTPUT="${STATUS_OUTPUT:-}" \
		EXIT_CODE_OUTPUT="${EXIT_CODE_OUTPUT:-}" \
		FAILURE_REASON="${FAILURE_REASON:-}" \
		bash "${SCRIPT_DIR}/is-infra-flake-failure.sh"; then
		echo "::warning::Treating upstream ${UPSTREAM_RESULT} as infra flake (non-blocking)"
		write_gate_outputs 0 passed
		exit 0
	fi

	echo "::error::Upstream job failed (result=${UPSTREAM_RESULT})"
	write_gate_outputs 1 failed
	exit 1
fi

if [[ -n "${PASSED_OUTPUT:-}" && "${PASSED_OUTPUT}" != "true" ]]; then
	echo "::error::Upstream passed output is not true (passed=${PASSED_OUTPUT})"
	write_gate_outputs 1 failed
	exit 1
fi

if [[ -n "${STATUS_OUTPUT:-}" && "${STATUS_OUTPUT}" != "${STATUS_EXPECTED}" ]]; then
	if UPSTREAM_RESULT=failure \
		UPSTREAM_CONCLUSION="${UPSTREAM_CONCLUSION:-}" \
		STATUS_OUTPUT="${STATUS_OUTPUT}" \
		EXIT_CODE_OUTPUT="${EXIT_CODE_OUTPUT:-}" \
		FAILURE_REASON="${FAILURE_REASON:-}" \
		bash "${SCRIPT_DIR}/is-infra-flake-failure.sh"; then
		echo "::warning::Treating upstream status ${STATUS_OUTPUT} as infra flake (non-blocking)"
		write_gate_outputs 0 passed
		exit 0
	fi

	echo "::error::Upstream status is not ${STATUS_EXPECTED} (status=${STATUS_OUTPUT})"
	write_gate_outputs 1 failed
	exit 1
fi

echo "Required check satisfied (upstream=${UPSTREAM_RESULT})"
write_gate_outputs 0 passed
