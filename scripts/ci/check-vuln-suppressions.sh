#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# check-vuln-suppressions.sh — Detect stale or expired vulnerability
# suppressions in .osv-scanner.toml and open a cleanup PR removing them.
#
# Usage:
#   scripts/ci/check-vuln-suppressions.sh
#
# Environment:
#   GH_TOKEN - GitHub token for PR creation (required)

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: check-vuln-suppressions.sh

Detect stale or expired vulnerability suppressions in .osv-scanner.toml.

Runs osv-scanner without suppressions to see which suppressed
vulnerabilities are still present. Opens a PR removing entries that
are stale (vuln resolved) or expired (past expiry date).

Requires GH_TOKEN for PR management.
EOF
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# Source shared utilities
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

OSV_TOML=".osv-scanner.toml"

if [[ ! -f "$OSV_TOML" ]]; then
	log_success "No $OSV_TOML found. Nothing to check."
	exit 0
fi

# Export deps and probe osv-scanner without suppressions
log_info "Exporting dependencies..."
REQUIREMENTS_FILE=$(mktemp --tmpdir requirements.XXXXXX.txt)
AWK_TMPFILE=$(mktemp --tmpdir osv-toml-rewrite.XXXXXX.toml)
# shellcheck disable=SC2064
# Intentional: expand $REQUIREMENTS_FILE and $AWK_TMPFILE now, not at trap time
trap "rm -f '$REQUIREMENTS_FILE' '$AWK_TMPFILE'" EXIT

uv export --no-emit-project >"$REQUIREMENTS_FILE"

log_info "Probing osv-scanner without suppressions..."
PROBE_OUTPUT=$(
	osv-scanner scan --format json --config /dev/null \
		--lockfile "$REQUIREMENTS_FILE" 2>&1 || true
)

# Classify suppressions using lintro's Python parser
log_info "Classifying suppressions..."
CLASSIFICATION_JSON=$(echo "$PROBE_OUTPUT" | uv run python3 "$SCRIPT_DIR/classify-suppressions.py")

# Extract IDs from JSON classification
STALE_IDS=()
EXPIRED_IDS=()
ACTIVE_IDS=()
while IFS= read -r id; do [[ -n "$id" ]] && STALE_IDS+=("$id"); done < <(echo "$CLASSIFICATION_JSON" | python3 -c "import json,sys; [print(i) for i in json.load(sys.stdin).get('stale',[])]")
while IFS= read -r id; do [[ -n "$id" ]] && EXPIRED_IDS+=("$id"); done < <(echo "$CLASSIFICATION_JSON" | python3 -c "import json,sys; [print(i) for i in json.load(sys.stdin).get('expired',[])]")
while IFS= read -r id; do [[ -n "$id" ]] && ACTIVE_IDS+=("$id"); done < <(echo "$CLASSIFICATION_JSON" | python3 -c "import json,sys; [print(i) for i in json.load(sys.stdin).get('active',[])]")

# Combine IDs to remove
REMOVE_IDS=("${STALE_IDS[@]+"${STALE_IDS[@]}"}" "${EXPIRED_IDS[@]+"${EXPIRED_IDS[@]}"}")

# Report
for id in "${ACTIVE_IDS[@]+"${ACTIVE_IDS[@]}"}"; do
	log_success "Active: $id"
done
for id in "${STALE_IDS[@]+"${STALE_IDS[@]}"}"; do
	log_warning "Stale: $id"
done
for id in "${EXPIRED_IDS[@]+"${EXPIRED_IDS[@]}"}"; do
	log_warning "Expired: $id"
done

# If everything is active, nothing to do
if [[ ${#REMOVE_IDS[@]} -eq 0 ]]; then
	log_success "All suppressions are active. Nothing to do."
	exit 0
fi

# Check if a PR already exists (fail on gh errors to avoid duplicates)
PR_LIST_OUTPUT=""
PR_LIST_EXIT=0
PR_LIST_OUTPUT=$(
	gh pr list --state open \
		--search "chore(security): remove stale vulnerability" \
		--json number --jq '.[0].number' 2>&1
) || PR_LIST_EXIT=$?
if [[ "$PR_LIST_EXIT" -ne 0 ]]; then
	log_error "gh pr list failed: $PR_LIST_OUTPUT"
	exit 1
fi
if [[ -n "$PR_LIST_OUTPUT" ]]; then
	log_info "Cleanup PR #${PR_LIST_OUTPUT} already open. Skipping."
	exit 0
fi

# --- Remove stale/expired entries from .osv-scanner.toml ---

for VULN_ID in "${REMOVE_IDS[@]}"; do
	log_info "Removing $VULN_ID from $OSV_TOML..."
	# Remove the [[IgnoredVulns]] block containing this ID.
	# Block format: [[IgnoredVulns]]\nid = "..."\n...\n
	# Use awk to skip the block.
	awk -v id="$VULN_ID" '
		/^\[\[IgnoredVulns\]\]/ {
			# Flush any pending block before starting a new one
			if (in_block && !found) {
				printf "%s", block
			}
			block = $0 "\n"
			in_block = 1
			found = 0
			next
		}
		in_block {
			# Boundary: blank line or new section header
			if (/^$/ || /^\[/) {
				if (!found) {
					printf "%s", block
				}
				in_block = 0
				found = 0
				block = ""
				print
				next
			}
			block = block $0 "\n"
			if ($0 ~ "id = \"" id "\"") {
				found = 1
			}
			next
		}
		{ print }
		END {
			if (in_block && !found) {
				printf "%s", block
			}
		}
	' "$OSV_TOML" >"$AWK_TMPFILE" && mv "$AWK_TMPFILE" "$OSV_TOML"
done

# Clean up empty TOML file (only comments/whitespace left)
if [[ -f "$OSV_TOML" ]]; then
	if ! grep -qE '^\[' "$OSV_TOML"; then
		log_info "No entries left in $OSV_TOML, removing file"
		rm -f "$OSV_TOML"
	fi
fi

# Check if any changes were made
if ! git diff --quiet; then
	# Build commit message and PR body
	REMOVED_LIST=""
	for id in "${REMOVE_IDS[@]}"; do
		REMOVED_LIST="${REMOVED_LIST}- \`${id}\`
"
	done

	BRANCH="chore/remove-stale-vulns-$(date +%Y%m%d%H%M%S)"
	git config user.name "github-actions[bot]"
	git config user.email "github-actions[bot]@users.noreply.github.com"
	git checkout -b "$BRANCH"
	git add "$OSV_TOML" 2>/dev/null || true
	git add -u
	git commit -m "$(
		cat <<EOF
chore(security): remove stale vulnerability suppressions

The following suppressions are no longer needed:
${REMOVED_LIST}
Detected by the weekly vuln-suppression-check workflow.
EOF
	)"

	git push -u origin "$BRANCH"

	WF_URL="${GITHUB_SERVER_URL:-https://github.com}"
	WF_URL="${WF_URL}/${GITHUB_REPOSITORY:-lgtm-hq/py-lintro}"
	WF_URL="${WF_URL}/actions/workflows/vuln-suppression-check.yml"

	gh pr create \
		--title "chore(security): remove stale vulnerability suppressions" \
		--body "$(
			cat <<EOF
## Summary
- Remove stale/expired vulnerability suppressions that are no longer needed

### Removed
${REMOVED_LIST}
## Test plan
- [ ] CI security audit passes without these suppressions
- [ ] osv-scanner scan passes without these suppressions

---
*Auto-created by [vuln-suppression-check](${WF_URL}).*
EOF
		)"

	log_success "Cleanup PR created on branch $BRANCH"
else
	log_info "No file changes needed."
fi
