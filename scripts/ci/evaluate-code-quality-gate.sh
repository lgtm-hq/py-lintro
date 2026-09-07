#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Evaluate docker-ci upstream jobs for the required lintro-code-quality gate.
#
# Selects the effective dogfooding lint attempt (retry when it ran) and writes
# normalized upstream values for assert-required-check.sh.
#
# This script only normalizes; the pass/fail verdict is written by
# run-code-quality-gate.sh. When neither attempt produced a lint verdict the
# normalized values carry that through (empty status/exit-code, or exit-code
# 143), and the gate fails closed with passed=false and infra-flake=true
# (#2296).
#
# Required environment variables:
#   DOCKER_BUILD_RESULT
#   PRIMARY_LINT_RESULT
#
# Optional environment variables:
#   RETRY_LINT_RESULT
#   PRIMARY_LINT_STATUS, PRIMARY_LINT_EXIT_CODE, PRIMARY_LINT_CONCLUSION
#   RETRY_LINT_STATUS, RETRY_LINT_EXIT_CODE, RETRY_LINT_CONCLUSION
#   PRIMARY_LINT_TIMEOUT_FLAKE, PRIMARY_LINT_TIMED_OUT_TOOLS
#   RETRY_LINT_TIMEOUT_FLAKE, RETRY_LINT_TIMED_OUT_TOOLS
#
# Also writes verdict-source (docker-build or lint) naming the
# job the verdict came from, so lint-only evidence such as the tool-execution
# timeout proof (#1653) is never applied to an upstream build failure.
#
# Tool-execution timeout evidence (#1653, lgtm-ci#746). The reusable lint
# workflow computes timeout-flake / timed-out-tools from the authoritative
# run's OWN JSON report, so the verdict travels with the attempt it describes.
# This script therefore carries those two values through the SAME attempt
# selection as status/exit-code below: whichever attempt supplies the verdict
# also supplies the timeout evidence, and a stale flag from the losing attempt
# is never paired with the winner's verdict.
#
# Changed-files asymmetry (decided, not an omission). The changed-scope job
# (scripts/ci/dogfood-changed-files.sh) publishes no JSON report and therefore
# no timeout verdict; the caller passes an empty PRIMARY_LINT_TIMEOUT_FLAKE in
# that scope. Changed-scope runs lint a handful of files, so a per-tool timeout
# there is both unlikely and worth a human look — it stays fail-closed (red).

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Evaluate docker-ci upstream jobs for the required lintro-code-quality gate.

Usage:
  DOCKER_BUILD_RESULT=success \
    PRIMARY_LINT_RESULT=success scripts/ci/evaluate-code-quality-gate.sh

Writes upstream-result, status-output, exit-code-output,
upstream-conclusion, verdict-source, timeout-flake-output, and
timed-out-tools-output to GITHUB_OUTPUT when set.
verdict-source is docker-build or lint — it names the job the
verdict came from so callers can scope lint-only evidence to a lint verdict.
timeout-flake-output is the effective attempt's own tool-execution timeout
verdict (#1653); it is 'true' only when that attempt's report proved a timeout
with zero findings, and 'false' for every other value.
EOF
	exit 0
fi

: "${DOCKER_BUILD_RESULT:?}"
: "${PRIMARY_LINT_RESULT:?}"

# GITHUB_OUTPUT is a line-oriented key=value file, so a newline inside a value
# would be parsed as a new record. These values are env-derived, and a value
# such as $'boom\nstatus-output=passed' would otherwise forge a passing
# verdict. Refuse to write instead of emitting a malformed record — the caller
# runs under `set -e`, so the gate fails closed (red) rather than green.
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
	write_output verdict-source docker-build
	# Lint-only evidence must never be applied to a build failure.
	write_output timeout-flake-output false
	write_output timed-out-tools-output ""
	exit 0
fi

# A genuine lint verdict is status=failed or exit-code=1 (lintro's only
# non-zero lint exit). Runner kills surface as 143, cancellations/timeouts as
# empty outputs — neither is a lint verdict.
reports_genuine_lint_failure() {
	[[ "${1}" == "failed" || "${2}" == "1" ]]
}

# Fail closed on anything that is not the exact literal 'true': an empty value
# (changed scope, a skipped classifier step, an upstream that predates the
# outputs) must never read as timeout evidence.
normalize_timeout_flake() {
	[[ "${1:-}" == "true" ]] && printf 'true' || printf 'false'
}

# The tool list is log-only. Restrict it to the shape lintro tool names can
# take so nothing env-derived reaches a log line (or a GITHUB_OUTPUT record)
# with unexpected characters.
sanitize_timed_out_tools() {
	printf '%s' "${1:-}" | LC_ALL=C tr -cd 'A-Za-z0-9_,.-'
}

effective_result="${PRIMARY_LINT_RESULT}"
effective_status="${PRIMARY_LINT_STATUS:-}"
effective_exit_code="${PRIMARY_LINT_EXIT_CODE:-}"
effective_conclusion="${PRIMARY_LINT_CONCLUSION:-}"
effective_timeout_flake="$(normalize_timeout_flake "${PRIMARY_LINT_TIMEOUT_FLAKE:-}")"
effective_timed_out_tools="$(sanitize_timed_out_tools "${PRIMARY_LINT_TIMED_OUT_TOOLS:-}")"

# The retry (full-run only) exists to give a genuinely flaked primary a second
# chance, so it becomes authoritative only when it is itself authoritative:
#   - it passed (the tree is clean; a real violation is deterministic and would
#     have failed the retry too), or
#   - it reported its own genuine lint failure.
# It must NOT override the primary when the retry itself flaked (killed at 143,
# cancelled, empty outputs): otherwise a primary that reported failed/1 would be
# replaced by an absorbable retry result and the real failure would be masked
# (Greptile P1 on #1650). When the primary already flaked too, we prefer the
# retry as the later of two non-verdicts. Since #2296 that terminal case is
# fail-closed rather than absorbed: run-code-quality-gate.sh writes
# passed=false / status=no-verdict / infra-flake=true and the required check
# goes red until the auto-rerun produces a real verdict.
if [[ "${RETRY_LINT_RESULT:-}" == "success" || "${RETRY_LINT_RESULT:-}" == "failure" ]]; then
	if [[ "${RETRY_LINT_RESULT}" == "success" ]] ||
		reports_genuine_lint_failure "${RETRY_LINT_STATUS:-}" "${RETRY_LINT_EXIT_CODE:-}" ||
		! reports_genuine_lint_failure "${PRIMARY_LINT_STATUS:-}" "${PRIMARY_LINT_EXIT_CODE:-}"; then
		effective_result="${RETRY_LINT_RESULT}"
		effective_status="${RETRY_LINT_STATUS:-}"
		effective_exit_code="${RETRY_LINT_EXIT_CODE:-}"
		effective_conclusion="${RETRY_LINT_CONCLUSION:-}"
		effective_timeout_flake="$(
			normalize_timeout_flake "${RETRY_LINT_TIMEOUT_FLAKE:-}"
		)"
		effective_timed_out_tools="$(
			sanitize_timed_out_tools "${RETRY_LINT_TIMED_OUT_TOOLS:-}"
		)"
	fi
fi

if [[ "${effective_result}" == "success" ]]; then
	write_output upstream-result success
	write_output status-output "${effective_status:-passed}"
	write_output exit-code-output "${effective_exit_code:-0}"
	write_output upstream-conclusion success
	write_output verdict-source lint
	write_output timeout-flake-output "${effective_timeout_flake}"
	write_output timed-out-tools-output "${effective_timed_out_tools}"
	exit 0
fi

write_output upstream-result "${effective_result}"
write_output status-output "${effective_status}"
write_output exit-code-output "${effective_exit_code}"
write_output upstream-conclusion "${effective_conclusion}"
write_output verdict-source lint
write_output timeout-flake-output "${effective_timeout_flake}"
write_output timed-out-tools-output "${effective_timed_out_tools}"
