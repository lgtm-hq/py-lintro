#!/usr/bin/env bash
set -euo pipefail

# run-docker-test-suite.sh
#
# Run the Docker integration test suite and capture output for summary extraction.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Run Docker integration tests and capture output.

Usage:
  scripts/docker/run-docker-test-suite.sh [log-file]

Arguments:
  log-file  Output log path (default: test-output.log)
EOF
	exit 0
fi

log_file="${1:-test-output.log}"

./scripts/docker/docker-test.sh 2>&1 | tee "$log_file"
exit "${PIPESTATUS[0]}"
