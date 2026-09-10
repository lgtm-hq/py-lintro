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
#   TIMEOUT_FLAKE       - 'true' when the SAME lint attempt's own JSON report
#                         proves its only failures were tool-execution timeouts
#                         with zero findings anywhere (#1653)
#   TIMED_OUT_TOOLS     - Comma-separated tool names for the log message

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
  TIMEOUT_FLAKE         'true' when the same attempt's report proves a
                        tool-execution timeout with zero findings (#1653)
  TIMED_OUT_TOOLS       Comma-separated timed-out tool names (log only)
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
	local timeout_flake="${5:-}"

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

	# Tool-execution timeout in THIS attempt (#1653). A tool that exceeds its
	# execution timeout makes lintro exit 1 with status=failed — structurally
	# identical to a real verdict — so this branch, like the 143 branch, must
	# sit above the lint-verdict guard.
	#
	# Trusting it is only sound because the flag is derived from the
	# authoritative attempt's OWN JSON report (lgtm-ci#746, exposed since
	# reusable-quality-lint v0.63.7) and because that classifier fails closed:
	# 'true' requires at least one timed-out tool, zero issues from every tool,
	# and no non-timeout failure anywhere (classify-lint-timeout.py). Anything
	# else — a missing/malformed report, another tool failing, any finding —
	# yields 'false' or an empty value, both of which fall through and stay red.
	# The caller is responsible for passing only the effective attempt's own
	# flag and only for a lint verdict (run-code-quality-gate.sh scopes it by
	# verdict-source, so a docker-build failure can never be absorbed here).
	if [[ "${timeout_flake}" == "true" ]]; then
		echo "Tool-execution timeout in the authoritative lint run" \
			"(tools: ${TIMED_OUT_TOOLS:-unknown}); zero findings reported."
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

	# Lint completed and passed, yet the job still failed — e.g. a post-lint
	# step such as the report artifact upload flaking (non-fatal upstream
	# since lgtm-ci#696). The lint verdict is authoritative, so this is
	# non-lint (infra) noise.
	if [[ "${status_output}" == "passed" && "${exit_code_output}" == "0" ]]; then
		return 0
	fi

	# No free-text log matching here on purpose: a substring like ETIMEDOUT
	# appearing anywhere in a job log (including inside a lint report) must
	# never green the required check — the Greptile concern on #1650/#1655.
	# Infra classes are recognized only from structural signals above.
	return 1
}

if is_infra_flake_failure \
	"${UPSTREAM_RESULT}" \
	"${UPSTREAM_CONCLUSION:-}" \
	"${STATUS_OUTPUT:-}" \
	"${EXIT_CODE_OUTPUT:-}" \
	"${TIMEOUT_FLAKE:-}"; then
	exit 0
fi

exit 1
