#!/usr/bin/env bash

# Shared utilities for workflow scripts
# This file contains common functions and variables used across multiple scripts

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Common exclude directories for lintro
EXCLUDE_DIRS=".git,__pycache__,htmlcov,.pytest_cache,.mypy_cache,dist,build,node_modules,.venv,venv,.tox,coverage-report,lintro-report"

# Common environment variables
GITHUB_SERVER_URL="${GITHUB_SERVER_URL:-https://github.com}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}"
GITHUB_RUN_ID="${GITHUB_RUN_ID:-}"
GITHUB_SHA="${GITHUB_SHA:-}"

# Function to check if we're in a PR context
# Handles both pull_request and pull_request_target events
is_pr_context() {
	local event="${GITHUB_EVENT_NAME:-}"
	[ "$event" = "pull_request" ] || [ "$event" = "pull_request_target" ]
}

# Function to log messages with colors
log_info() {
	echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
	echo -e "${GREEN}✅ $1${NC}"
}

log_warning() {
	echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
	echo -e "${RED}❌ $1${NC}"
}

log_verbose() {
	[ "${VERBOSE:-0}" -eq 1 ] && echo -e "${BLUE}[verbose] $1${NC}" || true
}

# Function to generate PR comment file
# Usage: generate_pr_comment "title" "status" "content" "output_file" ["tool_name"]
generate_pr_comment() {
	local title="$1"
	local status="$2"
	local content="$3"
	local output_file="$4"
	local tool_name="${5:-lintro}"

	local comment="## $title

This PR has been analyzed using **$tool_name** - our unified code quality tool.

### 📊 Status: $status

$content

---
🔗 **[View full build details]($GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID)**

*This analysis was performed automatically by the CI pipeline.*"

	echo "$comment" >"$output_file"
	log_success "PR comment generated and saved to $output_file"
}

# Function to handle coverage percentage
get_coverage_value() {
	local coverage_percentage="${COVERAGE_PERCENTAGE:-0.0}"

	# Handle case where coverage might be empty or undefined
	if [ -z "$coverage_percentage" ] || [ "$coverage_percentage" = "0.0" ]; then
		echo "0.0"
	else
		echo "$coverage_percentage"
	fi
}

# Function to determine coverage status
get_coverage_status() {
	local coverage_value="$1"
	local threshold="${2:-80}"

	# Portable numeric comparison without requiring bc
	awk -v c="$coverage_value" -v t="$threshold" 'BEGIN{ exit (c>=t)?0:1 }' && echo "✅" || echo "⚠️"
}

# Function to run lintro with common options
run_lintro() {
	local command="$1"
	# SC2034: format is reserved for future use with --format flag
	# shellcheck disable=SC2034
	local format="${2:-grid}"
	local exclude="${3:-$EXCLUDE_DIRS}"

	if [ -n "$exclude" ]; then
		uv run lintro "$command" . --exclude "$exclude"
	else
		uv run lintro "$command" .
	fi
}

# Function to check if file exists and log result
check_file_exists() {
	local file="$1"
	local description="$2"

	if [ -f "$file" ]; then
		log_success "$description found"
		echo "File size: $(wc -c <"$file") bytes"
		return 0
	else
		log_warning "$description not found"
		return 1
	fi
}

# Function to check if directory exists and log result
check_dir_exists() {
	local dir="$1"
	local description="$2"

	if [ -d "$dir" ]; then
		log_success "$description found"
		echo "Files in $dir:"
		ls -la "$dir/"
		return 0
	else
		log_warning "$description not found"
		return 1
	fi
}

# =============================================================================
# GitHub Actions Integration Functions
# =============================================================================

# Configure git user for CI commits (github-actions[bot])
configure_git_ci_user() {
	git config user.name "github-actions[bot]"
	git config user.email "github-actions[bot]@users.noreply.github.com"
}

# Set a GitHub Actions output variable
# Usage: set_github_output "key" "value"
set_github_output() {
	local key="$1"
	local value="$2"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		echo "$key=$value" >>"$GITHUB_OUTPUT"
	fi
}

# Set a GitHub Actions environment variable
# Usage: set_github_env "key" "value"
set_github_env() {
	local key="$1"
	local value="$2"
	if [[ -n "${GITHUB_ENV:-}" ]]; then
		echo "$key=$value" >>"$GITHUB_ENV"
	fi
}

# =============================================================================
# Utility Functions
# =============================================================================

# Array to track temporary directories for cleanup
_TEMP_DIRS=()

# Cleanup function for temporary directories
_cleanup_temp_dirs() {
	for dir in "${_TEMP_DIRS[@]}"; do
		rm -rf "$dir"
	done
}

# Create a temporary directory with automatic cleanup on exit
# Multiple calls accumulate directories instead of overwriting the trap
# Chains with any existing EXIT trap to avoid clobbering other cleanup handlers
# Usage: tmpdir=$(create_temp_dir)
create_temp_dir() {
	local tmpdir
	tmpdir=$(mktemp -d)
	_TEMP_DIRS+=("$tmpdir")
	# Chain with existing EXIT trap instead of clobbering it
	local existing_trap
	existing_trap=$(trap -p EXIT | sed -n "s/^trap -- '\(.*\)' EXIT$/\1/p")
	if [ -n "$existing_trap" ]; then
		# SC2064: We intentionally expand $existing_trap now to capture its value
		# shellcheck disable=SC2064
		# Guard against duplicate trap chaining
		case "$existing_trap" in
		*"_cleanup_temp_dirs"*) trap "$existing_trap" EXIT ;;
		*) trap "$existing_trap; _cleanup_temp_dirs" EXIT ;;
		esac
	else
		trap _cleanup_temp_dirs EXIT
	fi
	echo "$tmpdir"
}

# Display standardized help message
# Usage: show_help "script_name" "description" "usage_pattern"
show_help() {
	local script_name="$1"
	local description="$2"
	local usage="${3:-}"
	cat <<EOF
Usage: $script_name $usage

$description

Options:
  --help, -h    Show this help message
EOF
}

# Extract coverage percentage from coverage.xml
# Usage: percentage=$(get_coverage_percentage "coverage.xml")
get_coverage_percentage() {
	local coverage_file="${1:-coverage.xml}"
	if [[ -f "$coverage_file" ]]; then
		# Extract line-rate attribute and convert to percentage
		# Use portable sed instead of grep -oP for macOS/BSD compatibility
		local line_rate
		line_rate=$(sed -n 's/.*line-rate="\([^"]*\)".*/\1/p' "$coverage_file" | head -n1)
		line_rate=${line_rate:-0}
		awk -v lr="$line_rate" 'BEGIN { printf "%.2f", lr * 100 }'
	else
		echo "0.00"
	fi
}

# =============================================================================
# Version Comparison Functions
# =============================================================================

# Compare two semantic versions (portable, no sort -V required)
# Returns 0 if version1 >= version2, 1 otherwise
# Usage: version_ge "1.2.3" "1.2.0" && echo "yes" || echo "no"
version_ge() {
	local version1="$1"
	local version2="$2"

	# Use awk for portable numeric comparison of version components
	awk -v v1="$version1" -v v2="$version2" '
	BEGIN {
		# Split versions into components
		n1 = split(v1, a1, ".")
		n2 = split(v2, a2, ".")

		# Compare each component
		max = (n1 > n2) ? n1 : n2
		for (i = 1; i <= max; i++) {
			# Default missing components to 0
			c1 = (i <= n1) ? a1[i] + 0 : 0
			c2 = (i <= n2) ? a2[i] + 0 : 0

			if (c1 > c2) exit 0  # version1 > version2
			if (c1 < c2) exit 1  # version1 < version2
		}
		exit 0  # versions are equal
	}'
}

# =============================================================================
# Checksums
# =============================================================================

# Print the SHA256 of a file, using whichever of sha256sum/shasum exists.
# Returns 1 when neither is available so callers can fail closed with their own
# message. Single implementation so the Linux (sha256sum) and macOS (shasum)
# release paths cannot drift apart.
# Usage: sha="$(sha256_file "$path")" || log_error "..."
sha256_file() {
	local file="$1"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$file" | cut -d' ' -f1
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$file" | cut -d' ' -f1
	else
		return 1
	fi
}

# =============================================================================
# Release Assets (#2435)
# =============================================================================
#
# Shared gh helpers for the crash-safe release-asset swap, used by
# scripts/build/upload_release_asset.sh (which publishes the swap) and
# scripts/build/reuse_release_asset.sh (which finishes an interrupted one), so
# the two agree on what a staging asset is called and how it is promoted.
# Require `gh` on PATH and a resolvable repository (GH_REPO or a git remote).

# Name the swap stages an asset under while it is being uploaded.
release_staging_name() {
	printf '%s.new\n' "$1"
}

# Echo the numeric id of a named asset on the release, or nothing when absent.
# Usage: release_asset_id <tag> <name>
release_asset_id() {
	local tag="$1"
	local name="$2"
	gh api "repos/{owner}/{repo}/releases/tags/${tag}" \
		--jq ".assets[] | select(.name == \"${name}\") | .id" 2>/dev/null || true
}

# Usage: release_delete_asset <asset-id>
release_delete_asset() {
	local id="$1"
	gh api -X DELETE "repos/{owner}/{repo}/releases/assets/${id}" >/dev/null
}

# Download a published asset into <dir> and echo its path, or return 1 when the
# release has no such asset. Usage: release_download_asset <tag> <name> <dir>
release_download_asset() {
	local tag="$1"
	local name="$2"
	local dir="$3"
	rm -rf "$dir"
	mkdir -p "$dir"
	gh release download "$tag" \
		--pattern "$name" \
		--dir "$dir" \
		--clobber >/dev/null 2>&1 || return 1
	[[ -f "${dir}/${name}" ]] || return 1
	printf '%s\n' "${dir}/${name}"
}

# Delete whatever currently holds <final-name> and rename the staging asset
# onto it. This is the only part of the swap that can leave the release without
# the final name, and it is a delete plus a rename rather than a multi-second
# upload. Usage: release_promote_asset <tag> <staging-id> <final-name>
release_promote_asset() {
	local tag="$1"
	local staging_id="$2"
	local final_name="$3"
	local old_id
	old_id="$(release_asset_id "$tag" "$final_name")"
	if [[ -n "$old_id" ]]; then
		log_info "Replacing existing ${final_name}"
		release_delete_asset "$old_id"
	fi
	gh api -X PATCH "repos/{owner}/{repo}/releases/assets/${staging_id}" \
		-f "name=${final_name}" >/dev/null
}
