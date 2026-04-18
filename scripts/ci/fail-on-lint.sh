#!/usr/bin/env bash
set -euo pipefail

# fail-on-lint.sh
# Fail the CI job with a clear message when lint checks did not pass.
#
# Usage:
#   CHK_EXIT_CODE=<code> scripts/ci/fail-on-lint.sh

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Fail the step when lint checks failed.

Usage:
  CHK_EXIT_CODE=<code> scripts/ci/fail-on-lint.sh

Environment:
  CHK_EXIT_CODE  Exit code from previous lint step (non-zero means failure)
EOF
	exit 0
fi

code="${CHK_EXIT_CODE:-}"
if [[ -z "${code}" ]]; then
	echo "CHK_EXIT_CODE is required" >&2
	exit 2
fi

# Only fail if exit code is non-zero
if [[ "${code}" != "0" ]]; then
	echo "❌ Linting checks failed (exit code: ${code})"
	echo ""

	# Display actual linting errors if available. Lintro's OutputManager
	# writes console.log to .lintro/run-<timestamp>/ on every run; pick the
	# newest one so the failure context matches this invocation.
	latest_run_dir=$(find .lintro -maxdepth 1 -type d -name 'run-*' -print0 2>/dev/null |
		xargs -0 ls -dt 2>/dev/null | head -n1)
	if [[ -n "${latest_run_dir}" && -f "${latest_run_dir}/console.log" ]]; then
		echo "=== Linting Output (${latest_run_dir}/console.log) ==="
		cat "${latest_run_dir}/console.log"
		echo ""
		echo "=== End of Linting Output ==="
	else
		echo "⚠️  No console.log found under .lintro/run-*."
		echo "Check the build logs above for details."
	fi

	echo ""
	echo "Please fix the issues identified by lintro and try again."
	exit 1
fi
