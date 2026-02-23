#!/usr/bin/env bash
# Generate a JSON matrix of release tags for a given batch number.
# Used by backfill-docker-tags.yml to split 131+ releases into batches.
#
# Environment variables:
#   BATCH  - Batch number (1-6)
#
# Outputs (via GITHUB_OUTPUT):
#   tags   - JSON array of {tag, sha} objects
#   count  - Number of tags in this batch
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: BATCH=1 ./scripts/ci/backfill-generate-matrix.sh

Generates a JSON matrix of release tags for backfill-docker-tags.yml.
Splits all version tags into 6 batches and outputs the requested batch.

Required environment variables:
  BATCH   Batch number (1-6)
EOF
	exit 0
fi

if [[ -z "${BATCH:-}" ]]; then
	echo "::error::BATCH environment variable is required"
	exit 1
fi

# List all version tags, sorted chronologically, excluding action tags
mapfile -t all_tags < <(
	git tag --sort=creatordate --format='%(refname:short)' |
		grep -v 'actions-v'
)
total=${#all_tags[@]}
batch_size=$(((total + 5) / 6)) # ceil(total/6)
start=$(((BATCH - 1) * batch_size))
end=$((start + batch_size))
if ((end > total)); then end=$total; fi

# Build JSON array of tags for this batch
json="["
count=0
for ((i = start; i < end; i++)); do
	tag="${all_tags[$i]}"
	sha=$(git rev-parse --short "$tag")
	if ((count > 0)); then json+=","; fi
	json+="{\"tag\":\"${tag}\",\"sha\":\"${sha}\"}"
	((count++))
done
json+="]"

echo "tags=${json}" >>"$GITHUB_OUTPUT"
echo "count=${count}" >>"$GITHUB_OUTPUT"
echo "Batch ${BATCH}: ${count} tags (indices ${start}–$((end - 1)))"
