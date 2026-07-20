#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# compute-new-manifest-tools.sh
#
# Print the comma-separated set of tool names a PR *introduces* into
# lintro/tools/manifest.json, computed as the git diff of tool names between
# the current manifest and the manifest at the merge-base with the PR base
# branch. The manifest-vs-image gate (verify-image-manifest-tools.sh) feeds
# this to verify-manifest-tools.py via --allow-missing so a newly-added tool's
# (necessarily) absent binary in the digest-pinned base image downgrades to a
# warning instead of structurally failing the required check (#1565). The
# post-merge tools-image republish + digest bump restores full coverage.
#
# Fail CLOSED: any trouble resolving the base ref, merge-base, the old manifest
# blob, or the name diff prints an EMPTY set. An empty allowlist means full
# enforcement — the safe default. On main / nightly runs (no BASE_REF, no PR
# context) the set is likewise empty, so enforcement stays total.
#
# Fork PRs: the docker-ci checkout uses fetch-depth 0 and the base ref is a
# same-repo branch (github.base_ref, e.g. main) whose remote-tracking ref is
# fetched by the full checkout, so merge-base resolves without needing the
# fork's own history. If it cannot (shallow/absent base), the fail-closed path
# yields an empty allowlist and the gate enforces fully.
#
# Usage:
#   BASE_REF=main scripts/ci/compute-new-manifest-tools.sh

show_help() {
	cat <<'EOF'
Usage:
  BASE_REF=<branch> scripts/ci/compute-new-manifest-tools.sh

Print the comma-separated tool names a PR adds to the manifest, diffed against
the merge-base with the base branch. Fails closed (empty output) on any error.

Environment:
  BASE_REF   Optional. PR base branch (github.base_ref), e.g. main. When unset
             or empty (main / nightly runs), the added set is empty.
  MANIFEST   Optional. Manifest path relative to the repo root
             (default: lintro/tools/manifest.json).

Output:
  A single line on stdout: comma-separated added tool names (possibly empty).

Exit codes:
  0  always (fail-closed: errors print an empty set and still exit 0)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

: "${BASE_REF:=}"
: "${MANIFEST:=lintro/tools/manifest.json}"

log_info() { echo "[INFO] $*" >&2; }
log_warn() { echo "[WARN] $*" >&2; }

# Emit the (possibly empty) allowlist on stdout and exit 0. Every fail-closed
# path funnels through here so stdout carries exactly one line.
emit() {
	printf '%s\n' "${1:-}"
	exit 0
}

# No PR context (main push / nightly): full enforcement, empty allowlist.
if [[ -z "$BASE_REF" ]]; then
	log_info "No BASE_REF (not a PR context); empty allow-missing set"
	emit ""
fi

# Resolve the base ref: prefer the remote-tracking ref (CI checkouts), fall
# back to a local branch (local runs and tests).
base_commit=""
if git rev-parse --verify --quiet "origin/${BASE_REF}^{commit}" >/dev/null 2>&1; then
	base_commit="origin/${BASE_REF}"
elif git rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null 2>&1; then
	base_commit="${BASE_REF}"
fi

if [[ -z "$base_commit" ]]; then
	log_warn "Base ref '${BASE_REF}' not resolvable; failing closed (empty set)"
	emit ""
fi

if ! merge_base="$(git merge-base "$base_commit" HEAD 2>/dev/null)"; then
	log_warn "merge-base(${base_commit}, HEAD) failed; failing closed (empty set)"
	emit ""
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

old_manifest="$(mktemp)"
trap 'rm -f "$old_manifest"' EXIT

# The manifest may not have existed at the merge-base (brand-new manifest); an
# empty old blob makes compute-new-manifest-tools.py treat every current tool
# as added, which is the correct fail-open-to-tolerance for that rare case.
if ! git show "${merge_base}:${MANIFEST}" >"$old_manifest" 2>/dev/null; then
	log_info "No manifest at merge-base ${merge_base}; treating all tools as new"
	rm -f "$old_manifest"
fi

added=""
if ! added="$(python3 "${script_dir}/compute-new-manifest-tools.py" \
	--old "$old_manifest" --new "$MANIFEST")"; then
	log_warn "Name diff failed; failing closed (empty set)"
	emit ""
fi

if [[ -n "$added" ]]; then
	log_info "Tools newly added by this PR: ${added}"
else
	log_info "No tools newly added by this PR"
fi
emit "$added"
