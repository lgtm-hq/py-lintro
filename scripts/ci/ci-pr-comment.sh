#!/usr/bin/env bash
set -euo pipefail

# CI PR Comment Script
# Generates the lintro PR comment from the newest .lintro/run-*/report.md
# artifact produced by the lint job. The lint job uploads .lintro/ as a
# workflow artifact; this job downloads it before invoking this script, so
# the single source of truth is report.md (no log scraping required).

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FORMATTER_SCRIPT="$SCRIPT_DIR/format-lintro-pr-comment.py"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	cat <<'EOF'
Usage: ci-pr-comment.sh [--help|-h]

Generates pr-comment.txt from the newest .lintro/run-*/report.md produced
by the lint job (downloaded as the "lintro-run" artifact in the comment job).
Falls back to an "output unavailable" comment when the artifact is missing.

Environment:
  CHK_EXIT_CODE  Optional lint exit code (from the lint job's GITHUB_ENV).
EOF
	exit 0
fi

# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if ! is_pr_context; then
	log_info "Not in a PR context, skipping comment generation"
	exit 0
fi

if [ "${LINT_JOB_RESULT:-}" = "cancelled" ]; then
	CONTENT="<!-- lintro-report -->

**Workflow:**
1. ⚠️ Lint job was cancelled before \`lintro format\` and \`lintro check\` could complete.

Lintro did not produce a report for this run because the upstream lint job was cancelled.

Please inspect the workflow run to determine whether this was superseded by a newer run or interrupted by CI."
	generate_pr_comment "🔧 Lintro Code Quality Analysis" "⚠️ CANCELLED" "$CONTENT" "pr-comment.txt"
	exit 0
fi

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
STATUS_FILE="$TMP_DIR/lintro-comment-status.txt"
CONTENT_FILE="$TMP_DIR/lintro-comment-content.md"

write_unavailable_payload() {
	local reason="$1"
	local details="${2:-}"
	log_warning "$reason"
	python3 "$FORMATTER_SCRIPT" \
		--fallback-reason "$reason" \
		--details "$details" \
		--status-file "$STATUS_FILE" \
		--content-file "$CONTENT_FILE"
}

# Newest run directory wins — OutputManager mints a fresh run-<timestamp>/
# per invocation, so "newest" == "this run" under the lint-job workdir.
# Tolerate empty results / missing .lintro under set -euo pipefail.
REPORT_MD=""
if [ -d .lintro ]; then
	REPORT_MD=$({ find .lintro -maxdepth 2 -type f -name 'report.md' -path '.lintro/run-*/*' -print0 2>/dev/null || true; } |
		{ xargs -0 ls -t 2>/dev/null || true; } |
		head -n1 || true)
fi

if [ -n "$REPORT_MD" ] && [ -f "$REPORT_MD" ]; then
	log_info "Building PR comment from $REPORT_MD"
	python3 "$FORMATTER_SCRIPT" \
		--report-md "$REPORT_MD" \
		--exit-code "${CHK_EXIT_CODE:-}" \
		--status-file "$STATUS_FILE" \
		--content-file "$CONTENT_FILE"
else
	write_unavailable_payload \
		"The lintro run artifact (.lintro/run-*/report.md) was not available in the comment job." \
		"Ensure the lint job uploaded the lintro-run artifact and that this job downloaded it before invoking ci-pr-comment.sh."
fi

STATUS=$(cat "$STATUS_FILE")
CONTENT=$(cat "$CONTENT_FILE")

CONTENT="<!-- lintro-report -->

**Workflow:**
1. ✅ Applied formatting fixes with \`lintro format\`
2. 🔍 Performed code quality checks with \`lintro check\`

$CONTENT
"

generate_pr_comment "🔧 Lintro Code Quality Analysis" "$STATUS" "$CONTENT" "pr-comment.txt"
