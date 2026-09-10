#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Fail when an upstream reusable job did not pass required outputs.
#
# Fail-closed contract (#2296). Runner noise is still *classified* separately
# from a lint failure, but it is no longer absorbed into a green required
# check: an upstream job that never produced a lint verdict (runner loss,
# cancellation, SIGTERM exit 143, tool-execution timeout) writes
# `status=no-verdict infra-flake=true` and exits 1. The `infra-flake` output
# stays so the rerun bot and dashboards can tell "lint failed" apart from
# "lint did not run"; `auto-rerun-on-infra-failure.yml` retries the run.
#
# The one failure still absorbed is the mirror image: lint itself reported a
# passing verdict (`status=passed` / `exit-code=0`) and only a post-lint step
# of the surrounding job failed. That is a real verdict, so the check stays
# green with `infra-flake=true` and image promotion still refuses it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Fail when an upstream reusable job did not pass required outputs.

Upstream results that produced no lint verdict (cancellation, runner loss,
tool-execution timeout) fail closed: status=no-verdict, infra-flake=true,
exit 1 (#2296).

Usage:
  UPSTREAM_RESULT=success scripts/ci/assert-required-check.sh

Environment variables:
  UPSTREAM_RESULT       Upstream job result (required)
  UPSTREAM_CONCLUSION   Upstream job conclusion (optional)
  PASSED_OUTPUT         When non-empty, must be the string true
  STATUS_OUTPUT         When non-empty, must equal STATUS_EXPECTED unless infra flake
  STATUS_EXPECTED       Expected STATUS_OUTPUT value (default: passed)
  EXIT_CODE_OUTPUT      Upstream lint exit code for infra flake detection
  TIMEOUT_FLAKE         'true' when the same lint attempt's own report proves a
                        tool-execution timeout with zero findings (#1653)
  TIMED_OUT_TOOLS       Comma-separated timed-out tool names (log only)

Writes exit-code, status, and infra-flake to GITHUB_OUTPUT when set.
infra-flake=true means the failure was runner noise rather than a lint
violation; status=no-verdict means no lint verdict was produced at all and
the check is red so the auto-rerun can retry. The only green infra-flake is
a post-lint job failure on top of a passing lint verdict, which consumers
that need proof of a complete run (image promotion) must still reject.
EOF
	exit 0
fi

write_gate_outputs() {
	local exit_code="$1"
	local status="$2"
	local infra_flake="${3:-false}"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		{
			echo "exit-code=${exit_code}"
			echo "status=${status}"
			echo "infra-flake=${infra_flake}"
		} >>"${GITHUB_OUTPUT}"
	fi
}

# The one infra class that still greens the check: lint itself returned a
# passing verdict and only the surrounding job failed afterwards (e.g. the
# report artifact upload). Every other absorbed class means no lint verdict.
reports_passing_lint_verdict() {
	[[ "${STATUS_OUTPUT:-}" == "passed" && "${EXIT_CODE_OUTPUT:-}" == "0" ]]
}

# Fail closed on runner noise that produced no lint verdict (#2296). The
# message below is a fixed-string signature in
# .github/workflows/auto-rerun-on-infra-failure.yml — keep them in step.
fail_closed_without_verdict() {
	echo "::error::No lint verdict (runner loss); auto-rerun will retry" \
		"(${1})"
	write_gate_outputs 1 no-verdict true
	exit 1
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
		TIMEOUT_FLAKE="${TIMEOUT_FLAKE:-}" \
		TIMED_OUT_TOOLS="${TIMED_OUT_TOOLS:-}" \
		bash "${SCRIPT_DIR}/is-infra-flake-failure.sh"; then
		if reports_passing_lint_verdict; then
			echo "::warning::Upstream ${UPSTREAM_RESULT} after a passing lint" \
				"verdict; treating as infra flake (non-blocking)"
			write_gate_outputs 0 passed true
			exit 0
		fi
		fail_closed_without_verdict "result=${UPSTREAM_RESULT}"
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
		TIMEOUT_FLAKE="${TIMEOUT_FLAKE:-}" \
		TIMED_OUT_TOOLS="${TIMED_OUT_TOOLS:-}" \
		bash "${SCRIPT_DIR}/is-infra-flake-failure.sh"; then
		# Unreachable for a passing verdict: this branch only runs when
		# STATUS_OUTPUT differs from STATUS_EXPECTED (passed).
		fail_closed_without_verdict "status=${STATUS_OUTPUT}"
	fi

	echo "::error::Upstream status is not ${STATUS_EXPECTED} (status=${STATUS_OUTPUT})"
	write_gate_outputs 1 failed
	exit 1
fi

echo "Required check satisfied (upstream=${UPSTREAM_RESULT})"
write_gate_outputs 0 passed
