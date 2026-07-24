#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Classify upstream CI failures caused by runner infrastructure noise.
#
# Exits 0 when the failure is infrastructure noise, 1 when it is (or may be) a
# genuine lint failure. Classification is evidence-based and fails closed:
# missing lint outputs are never treated as infra noise.
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

# Safety contract for every branch below: an upstream failure may only be
# classified as infra when there is positive evidence that lint itself did not
# report a violation. A genuine lint failure always surfaces as
# status=failed / exit-code=1, so every branch either requires an explicit
# non-lint signal (cancelled, timed_out, SIGTERM exit 143) or requires the lint
# outputs to say the lint run passed. Absence of evidence — empty outputs —
# is never treated as infra: a job that never reported a lint verdict cannot be
# claimed to have passed one. The bounded `dogfooding_lint_retry` job is the
# remedy for that case (#1313).
reports_genuine_lint_failure() {
	local status_output="$1"
	local exit_code_output="$2"

	[[ "${status_output}" == "failed" || "${exit_code_output}" == "1" ]]
}

is_infra_flake_failure() {
	local result="$1"
	local conclusion="${2:-}"
	local status_output="${3:-}"
	local exit_code_output="${4:-}"
	local failure_reason="${5:-}"

	# Nothing to classify when the upstream job succeeded.
	if [[ "${result}" == "success" ]]; then
		return 1
	fi

	# Runner shutdown propagates SIGTERM to lintro, which exits 143. lintro
	# itself never exits 143 for a lint violation (it uses 1), so this is
	# checked before the lint-verdict guard: a SIGTERM'd run may still have
	# written status=failed on its way out.
	if [[ "${exit_code_output}" == "143" ]]; then
		return 0
	fi

	# Everything below is only reachable when lint did not report a violation.
	# This sits above the cancellation branch on purpose: a job cancelled
	# after lint already reported failed/1 has a real verdict, and absorbing
	# it would mask a genuine failure.
	if reports_genuine_lint_failure "${status_output}" "${exit_code_output}"; then
		return 1
	fi

	# Cancellation/timeout is a runner-level verdict: lint never returned one.
	if [[ "${result}" == "cancelled" || "${result}" == "timed_out" ]]; then
		return 0
	fi

	if [[ "${conclusion}" == "cancelled" || "${conclusion}" == "timed_out" ]]; then
		return 0
	fi

	# Lint completed and passed, yet the job still failed — e.g. the
	# `Upload linting report` step hitting `CreateArtifact: ETIMEDOUT`. The
	# lint verdict is authoritative, so this is non-lint (infra) noise.
	if [[ "${status_output}" == "passed" && "${exit_code_output}" == "0" ]]; then
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
