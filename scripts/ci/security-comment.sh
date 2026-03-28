#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# security-comment.sh — Run osv-scanner via lintro in Docker and generate
# a security-specific PR comment with vulnerability and suppression details.
#
# Usage:
#   scripts/ci/security-comment.sh
#
# Requires:
#   - py-lintro:latest Docker image (built by docker-build job)
#   - GITHUB_OUTPUT, GITHUB_STEP_SUMMARY (GitHub Actions env)

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: security-comment.sh

Run osv-scanner via lintro in Docker and generate a security PR comment.

The script runs `lintro check` with only osv-scanner enabled, parses the
JSON output for vulnerabilities and suppression staleness, and writes a
structured PR comment to security-audit-comment.txt.

Exit codes:
  0 - No vulnerabilities found
  1 - Vulnerabilities found or execution failed
EOF
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared utilities
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

COMMENT_FILE="security-audit-comment.txt"

log_info "Running osv-scanner via lintro in Docker..."

# Run lintro with osv-scanner only, JSON output, inside the Docker image.
# Mount the repo so osv-scanner can discover lockfiles across all ecosystems.
OSV_EXIT_CODE=0
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
	-v "$PWD:/code" -w /code py-lintro:latest \
	lintro check . --tools osv_scanner \
	--output-format json --output /code/osv-results.json \
	2>&1 | tee osv-output.txt || OSV_EXIT_CODE=$?

# Format JSON results as markdown PR comment body
format_err=$(mktemp)
FORMAT_FAILED=0
if ! COMMENT_BODY=$(python3 "$SCRIPT_DIR/format-security-comment.py" osv-results.json 2>"$format_err"); then
	log_error "format-security-comment.py failed:"
	cat "$format_err" >&2
	COMMENT_BODY="⚠️ Failed to format security audit results. See CI logs for details."
	FORMAT_FAILED=1
fi
rm -f "$format_err"

# Determine status: distinguish tool failures from actual vulnerabilities.
# lintro exits non-zero when issues are found; check the JSON output to
# determine whether vulnerabilities were actually reported vs a scan failure.
HAS_VULNS=0
AUDIT_FAILED=$FORMAT_FAILED
if [[ "$OSV_EXIT_CODE" -ne 0 ]]; then
	if [[ -f osv-results.json ]] && python3 -c "
import json, sys
try:
    d = json.load(open('osv-results.json'))
    r = next((x for x in d.get('results', []) if x.get('tool') == 'osv_scanner'), None)
    sys.exit(0 if r and r.get('issues_count', 0) > 0 else 1)
except (json.JSONDecodeError, KeyError) as e:
    print(f'Failed to parse osv-results.json: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1; then
		HAS_VULNS=1
	else
		log_info "osv-scanner exited non-zero but no valid vulnerability data found in osv-results.json"
		AUDIT_FAILED=1
	fi
fi

if [[ "$AUDIT_FAILED" -eq 1 ]]; then
	STATUS="⚠️ AUDIT FAILED"
elif [[ "$HAS_VULNS" -eq 1 ]]; then
	STATUS="⚠️ VULNERABILITIES FOUND"
else
	STATUS="✅ PASSED"
fi

CONTENT="<!-- security-audit-report -->

${COMMENT_BODY}"

# Generate the comment file using shared function
generate_pr_comment "🔐 Security Audit" "$STATUS" "$CONTENT" "$COMMENT_FILE" "lintro + osv-scanner"

# Only cleanup artifacts on full success (preserve for debugging on failure)
if [[ "$HAS_VULNS" -eq 0 && "$AUDIT_FAILED" -eq 0 ]]; then
	rm -f osv-results.json osv-output.txt
fi

# Expose result to downstream workflow steps (e.g. conditional notifications).
# Not consumed by current workflows but kept for future use.
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	echo "has_vulns=$HAS_VULNS" >>"$GITHUB_OUTPUT"
	echo "audit_failed=$AUDIT_FAILED" >>"$GITHUB_OUTPUT"
fi

if [[ "$AUDIT_FAILED" -eq 1 ]]; then
	log_error "Security audit failed (tool/scan error)"
	exit 1
elif [[ "$HAS_VULNS" -eq 1 ]]; then
	log_error "Security audit found vulnerabilities"
	exit 1
else
	log_success "Security audit passed"
	exit 0
fi
