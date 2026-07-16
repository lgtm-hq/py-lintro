#!/usr/bin/env bash
set -euo pipefail

# fail-on-security-audit.sh
#
# Fail the workflow step when the security audit reported vulnerabilities.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Fail the step when a security audit detected vulnerabilities.

Usage:
  scripts/ci/fail-on-security-audit.sh
EOF
	exit 0
fi

echo "::error::Security vulnerabilities detected in dependencies"
exit 1
