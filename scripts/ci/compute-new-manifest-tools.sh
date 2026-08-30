#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# compute-new-manifest-tools.sh
#
# Print the comma-separated set of tool names a PR changes in the manifest,
# computed as a git diff against the merge-base with the PR base branch.
# EMIT=added diffs the hand-authored lintro/tools/manifest.src.json (#2178);
# EMIT=version-changed diffs the rendered lintro/tools/manifest.json, which
# carries the version fields. The manifest-vs-image gate
# (verify-image-manifest-tools.sh) consumes this as:
#
#   EMIT=added (default) → the newly-added tool set (#2192). Those names are
#     verified in the app image like every other tool (the PR Dockerfile
#     bridge must install them if the pinned digest lacks them). An empty
#     set means full enforcement, never a skip.
#   EMIT=version-changed → --allow-version-lag: a baked tool whose manifest
#     version the PR bumps may still be older in the digest-pinned base image;
#     a version mismatch (image older than manifest) downgrades to a warning
#     (#1582). Missing binaries and image-newer-than-manifest still hard-fail.
#
# The post-merge tools-image republish + digest bump restores full coverage.
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
             or empty (main / nightly runs), the emitted set is empty.
  MANIFEST   Optional. Manifest path relative to the repo root. Defaults to
             lintro/tools/manifest.src.json for EMIT=added (new-tool detection
             needs only names, and the src file is the committed truth after
             #2178) and lintro/tools/manifest.json for EMIT=version-changed
             (version diffs need the rendered version fields).
  EMIT       Optional. ``added`` (default) or ``version-changed``. Selects
             which name set the Python helper prints.

Output:
  A single line on stdout: comma-separated tool names (possibly empty).

Exit codes:
  0  always (fail-closed: errors print an empty set and still exit 0)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

: "${BASE_REF:=}"
: "${EMIT:=added}"

# EMIT=added diffs names only, so it reads the committed hand-authored
# manifest.src.json (#2178) — which still exists at merge-bases after the
# rendered manifest stops being committed. EMIT=version-changed diffs the
# rendered version fields, which only manifest.json carries.
default_manifest="lintro/tools/manifest.src.json"
if [[ "$EMIT" == "version-changed" ]]; then
	default_manifest="lintro/tools/manifest.json"
fi
: "${MANIFEST:=$default_manifest}"

log_info() { echo "[INFO] $*" >&2; }
log_warn() { echo "[WARN] $*" >&2; }

# Emit the (possibly empty) allowlist on stdout and exit 0. Every fail-closed
# path funnels through here so stdout carries exactly one line.
emit() {
	printf '%s\n' "${1:-}"
	exit 0
}

case "$EMIT" in
added | version-changed) ;;
*)
	log_warn "Invalid EMIT='${EMIT}' (expected added|version-changed); failing closed"
	emit ""
	;;
esac

# No PR context (main push / nightly): full enforcement, empty allowlist.
if [[ -z "$BASE_REF" ]]; then
	log_info "No BASE_REF (not a PR context); empty ${EMIT} set"
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
# Transition window (#2178): a merge-base predating the manifest split has no
# manifest.src.json, so the added diff falls back to the rendered manifest
# there — names are identical in both files.
# Render the rendered manifest for a historic commit from that commit's own
# committed sources and generator (#2180: manifest.json is no longer
# committed, so version-changed diffs cannot ``git show`` it at the base).
# Prints the rendered manifest to the given output path; non-zero on any
# trouble (caller falls back).
render_manifest_at_ref() {
	local ref="$1"
	local out_path="$2"
	local worktree
	worktree="$(mktemp -d)"
	# Expand worktree now, not at EXIT time.
	# shellcheck disable=SC2064
	trap "rm -rf '$worktree'; rm -f '$old_manifest'" EXIT
	git archive "$ref" \
		lintro lintro_build scripts/ci \
		package.json pyproject.toml requirements-semgrep.txt \
		2>/dev/null | tar -x -C "$worktree" || return 1
	python3 "${worktree}/scripts/ci/generate-tool-versions.py" >/dev/null 2>&1 ||
		return 1
	cp "${worktree}/lintro/tools/manifest.json" "$out_path"
}

if ! git show "${merge_base}:${MANIFEST}" >"$old_manifest" 2>/dev/null; then
	if [[ "$EMIT" == "added" && "$MANIFEST" == "lintro/tools/manifest.src.json" ]] &&
		git show "${merge_base}:lintro/tools/manifest.json" >"$old_manifest" 2>/dev/null; then
		log_info "No ${MANIFEST} at merge-base ${merge_base}; using manifest.json (pre-split base)"
	elif [[ "$EMIT" == "version-changed" && "$MANIFEST" == "lintro/tools/manifest.json" ]] &&
		render_manifest_at_ref "$merge_base" "$old_manifest"; then
		log_info "Rendered merge-base manifest from ${merge_base} sources"
	else
		log_info "No manifest at merge-base ${merge_base}; treating all tools as new"
		rm -f "$old_manifest"
	fi
fi

names=""
if ! names="$(python3 "${script_dir}/compute-new-manifest-tools.py" \
	--old "$old_manifest" --new "$MANIFEST" --emit "$EMIT")"; then
	log_warn "Name diff failed; failing closed (empty set)"
	emit ""
fi

if [[ -n "$names" ]]; then
	log_info "Tools ${EMIT} by this PR: ${names}"
else
	log_info "No tools ${EMIT} by this PR"
fi
emit "$names"
