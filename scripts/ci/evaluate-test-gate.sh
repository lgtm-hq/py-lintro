#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Evaluate upstream test job results for the required org-ruleset gate.
# Fails when any upstream job reports "failure" or "cancelled"; treats
# "skipped" (draft PRs, pipeline-skip) as acceptable.
#
# Required environment variables:
#   COMPAT_RESULT  - needs.test-compat.result
#   COVERAGE_RESULT - needs.test-coverage.result

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Evaluate upstream test job results for the required org-ruleset gate.

Fails when any upstream job reports "failure" or "cancelled"; treats
"skipped" (draft PRs, pipeline-skip) as acceptable.

Usage:
  COMPAT_RESULT=success COVERAGE_RESULT=success scripts/ci/evaluate-test-gate.sh

Required environment variables:
  COMPAT_RESULT   needs.test-compat.result
  COVERAGE_RESULT needs.test-coverage.result
EOF
	exit 0
fi

: "${COMPAT_RESULT:?}"
: "${COVERAGE_RESULT:?}"

echo "test-compat:  ${COMPAT_RESULT}"
echo "test-coverage: ${COVERAGE_RESULT}"

failed=()
for job in "test-compat:${COMPAT_RESULT}" "test-coverage:${COVERAGE_RESULT}"; do
	name="${job%%:*}"
	result="${job##*:}"
	if [[ "${result}" == "failure" || "${result}" == "cancelled" ]]; then
		failed+=("${name} (${result})")
	fi
done

if ((${#failed[@]} > 0)); then
	printf '::error::Upstream test jobs failed: %s\n' "${failed[*]}"
	exit 1
fi

echo "All upstream test jobs passed or were skipped"
