#!/usr/bin/env bash
set -euo pipefail

# Coverage PR Comment Script
# Generates and posts comments to PRs with coverage and test summary information
#
# Note: Test summary parsing could be extracted to a shared utility if more
# scripts need to parse test-summary.json. Currently self-contained for simplicity.

# Show help if requested
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	echo "Usage: $0 [--help|-h]"
	echo ""
	echo "Coverage PR Comment Script"
	echo "Generates a PR comment with coverage and test info within a GitHub Actions run."
	echo ""
	echo "This script is intended for CI and will no-op outside pull_request events."
	exit 0
fi

# Source shared utilities
# shellcheck source=../../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$(dirname "$0")/../../utils/utils.sh"

# parse_json_field - Extract a field value from JSON file
# Uses jq if available, falls back to grep pattern matching.
#
# Args:
#   $1: JSON file path
#   $2: Field name (e.g., "passed", "total")
#   $3: Position filter - "first", "last", or "" for single match (default: "")
#
# Returns: The field value or "0" if not found
parse_json_field() {
	local file="$1"
	local field="$2"
	local position="${3:-}"

	# Try jq first if available
	if command -v jq >/dev/null 2>&1; then
		# Use jq to extract the field; handle nested objects by getting all matches
		local values
		values=$(jq -r ".. | objects | .$field // empty" "$file" 2>/dev/null | grep -v '^$' || true)
		if [ -n "$values" ]; then
			case "$position" in
			first) echo "$values" | head -1 ;;
			last) echo "$values" | tail -1 ;;
			*) echo "$values" | head -1 ;;
			esac
			return
		fi
	fi

	# Fallback to grep-based parsing
	# Pattern expects "key": value format with single space after colon
	local matches
	matches=$(grep -o "\"$field\": [0-9.]*" "$file" 2>/dev/null | grep -o '[0-9.]*' || true)
	if [ -n "$matches" ]; then
		case "$position" in
		first) echo "$matches" | head -1 ;;
		last) echo "$matches" | tail -1 ;;
		*) echo "$matches" | head -1 ;;
		esac
		return
	fi

	echo "0"
}

# validate_test_value - Check if a test count is valid (non-negative)
# Args:
#   $1: Value to validate
#   $2: Field name for logging
# Returns: The value if valid, "0" if invalid
validate_test_value() {
	local value="$1"
	local field_name="$2"

	# Check if value is a valid non-negative number (regex ensures non-negative)
	if ! [[ "$value" =~ ^[0-9]+\.?[0-9]*$ ]]; then
		log_warning "Invalid $field_name value: $value; using 0"
		echo "0"
		return
	fi

	echo "$value"
}

# Check if we're in a PR context
if ! is_pr_context; then
	log_info "Not in a PR context, skipping coverage comment generation"
	exit 0
fi

# Get coverage value using shared function
COVERAGE_VALUE=$(get_coverage_value)
COVERAGE_STATUS=$(get_coverage_status "$COVERAGE_VALUE")
JOB_RESULT_TEXT="${JOB_RESULT:-success}"
if [ "$JOB_RESULT_TEXT" != "success" ]; then
	BUILD_STATUS="❌ Tests failed"
else
	BUILD_STATUS="✅ Tests passed"
fi

# Determine status text
if [ "$COVERAGE_STATUS" = "✅" ]; then
	STATUS_TEXT="Target met (>80%)"
else
	STATUS_TEXT="Below target (<80%)"
fi

# Try to load test summary from JSON file if available
TEST_SUMMARY_TABLE=""
COVERAGE_DETAILS=""

if [ -f "test-summary.json" ]; then
	log_info "Loading test summary from test-summary.json"

	# Expected JSON structure from extract-test-summary.sh:
	# {
	#   "tests": { "passed": N, "failed": N, "skipped": N, "errors": N, "total": N, "duration": N.N },
	#   "coverage": { "percentage": N.N, "lines_covered": N, "lines_total": N, "lines_missing": N, "files": N }
	# }

	# Extract test values using parse_json_field (tries jq first, falls back to grep)
	TEST_PASSED=$(parse_json_field "test-summary.json" "passed")
	TEST_FAILED=$(parse_json_field "test-summary.json" "failed")
	TEST_SKIPPED=$(parse_json_field "test-summary.json" "skipped")
	TEST_ERRORS=$(parse_json_field "test-summary.json" "errors")
	TEST_TOTAL=$(parse_json_field "test-summary.json" "total" "first")
	TEST_DURATION=$(parse_json_field "test-summary.json" "duration")

	# Extract coverage details
	COV_LINES_COVERED=$(parse_json_field "test-summary.json" "lines_covered")
	COV_LINES_TOTAL=$(parse_json_field "test-summary.json" "lines_total")
	COV_LINES_MISSING=$(parse_json_field "test-summary.json" "lines_missing")
	COV_FILES=$(parse_json_field "test-summary.json" "files" "last")

	# Validate extracted values
	TEST_PASSED=$(validate_test_value "$TEST_PASSED" "TEST_PASSED")
	TEST_FAILED=$(validate_test_value "$TEST_FAILED" "TEST_FAILED")
	TEST_SKIPPED=$(validate_test_value "$TEST_SKIPPED" "TEST_SKIPPED")
	TEST_ERRORS=$(validate_test_value "$TEST_ERRORS" "TEST_ERRORS")
	TEST_TOTAL=$(validate_test_value "$TEST_TOTAL" "TEST_TOTAL")

	# Validate logical consistency
	if [ "${TEST_TOTAL:-0}" -gt 0 ]; then
		# Check if passed exceeds total
		if [ "${TEST_PASSED:-0}" -gt "${TEST_TOTAL}" ]; then
			log_warning "Extracted TEST_PASSED ($TEST_PASSED) > TEST_TOTAL ($TEST_TOTAL); resetting to 0"
			TEST_PASSED=0
			TEST_FAILED=0
			TEST_SKIPPED=0
			TEST_ERRORS=0
		fi
		# Sanity check: passed + failed + skipped + errors should not exceed total
		SUM=$((TEST_PASSED + TEST_FAILED + TEST_SKIPPED + TEST_ERRORS))
		if [ "$SUM" -gt "${TEST_TOTAL}" ]; then
			log_warning "Test counts sum ($SUM) exceeds total ($TEST_TOTAL); data may be inconsistent"
		fi
	fi

	# Format duration
	DURATION_STR="${TEST_DURATION}s"

	# Determine test status emoji
	if [ "${TEST_TOTAL:-0}" -eq 0 ]; then
		TEST_STATUS_EMOJI="⚠️ NO DATA"
	elif [ "${TEST_FAILED:-0}" -gt 0 ] || [ "${TEST_ERRORS:-0}" -gt 0 ]; then
		TEST_STATUS_EMOJI="❌ FAIL"
	else
		TEST_STATUS_EMOJI="✅ PASS"
	fi

	# Build test summary table
	TEST_SUMMARY_TABLE="### 🧪 Test Results

| Tool | Status | Passed | Failed | Errors | Skipped | Total | Duration |
|------|--------|--------|--------|--------|---------|-------|----------|
| 🧪 pytest | $TEST_STATUS_EMOJI | $TEST_PASSED | $TEST_FAILED | $TEST_ERRORS | $TEST_SKIPPED | $TEST_TOTAL | $DURATION_STR |
"

	# Build coverage details section
	if [ "${COV_LINES_TOTAL:-0}" -gt 0 ]; then
		COVERAGE_DETAILS="### 📊 Coverage Summary

| Metric | Value |
|--------|-------|
| Coverage | **${COVERAGE_VALUE}%** |
| Lines Covered | ${COV_LINES_COVERED} / ${COV_LINES_TOTAL} |
| Lines Missing | ${COV_LINES_MISSING} |
| Files Analyzed | ${COV_FILES} |
"
	fi
else
	log_warning "test-summary.json not found, using basic format"
fi

# Create the comment content with marker
CONTENT="<!-- coverage-report -->

**Build:** $BUILD_STATUS

**Coverage:** $COVERAGE_STATUS **$COVERAGE_VALUE%**

**Status:** $STATUS_TEXT

$TEST_SUMMARY_TABLE
$COVERAGE_DETAILS
### 📋 Coverage Details
- **Generated:** $(date +%Y-%m-%d)
- **Commit:** [$GITHUB_SHA](https://github.com/$GITHUB_REPOSITORY/commit/$GITHUB_SHA)

### 📁 View Detailed Report
**Direct Link:** [📊 HTML Coverage Report](https://github.com/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/artifacts)

Or download manually:
1. Go to the [Actions tab](https://github.com/$GITHUB_REPOSITORY/actions)
2. Find this workflow run
3. Download the \"coverage-report-python-3.14\" artifact
4. Extract and open \`index.html\` in your browser"

# Generate PR comment using shared function (always produce the file before posting)
generate_pr_comment "📊 Code Coverage Report" "$STATUS_TEXT" "$CONTENT" "coverage-pr-comment.txt"
