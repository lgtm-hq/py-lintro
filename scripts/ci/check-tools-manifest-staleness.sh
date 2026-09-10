#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# check-tools-manifest-staleness.sh
#
# Refuse to promote a tools-image candidate that was built before a tool
# manifest change landed on main (#2497).
#
# The candidate image is built from a Renovate branch and retagged as :latest
# after that branch merges. If an unrelated manifest change lands on main in
# between, the promoted :latest ships binaries that no longer match the
# manifest the image-vs-manifest gate (scripts/ci/verify-image-manifest-tools.sh)
# checks against — the drift that produced #2497. This guard compares the
# manifest inputs at the candidate's build commit with the same paths at the
# current main SHA and fails the promote before anything is retagged.
#
# Why a content diff rather than a bare `git log <candidate>..<main>`: the
# candidate branch is squash-merged, so main always carries a *new* commit
# touching the manifest (the candidate's own squash). A commit list alone
# would refuse every promote. The content comparison answers the question that
# actually matters — "do the manifest inputs on main still match what this
# image was built from?" — and the commit list is then reported for context.
#
# Where the candidate commit comes from: the candidate tag
# (tools-candidate-pr<N>-<sha12>) embeds the Renovate branch head SHA the
# image was built from — abbreviated to 12 characters — plus the PR number,
# and scripts/ci/promote-tools-candidate.py exports both as `candidate-sha`
# and `candidate-pr`. The PR number matters: git cannot fetch by an
# abbreviated object id, so the guard fetches refs/pull/<N>/head and resolves
# the abbreviation against the objects that brings in.
#
# The build also records the same commit as the
# org.opencontainers.image.revision OCI label (docker/metadata-action's
# default label set in the lgtm-ci reusable), but that label lives on the
# per-platform child configs of the published index, so reading it would cost
# an extra authenticated registry fetch for a value the tag already carries.

show_help() {
	cat <<'EOF'
Refuse promotion of a tools-image candidate built from a stale manifest.

Usage:
  CANDIDATE_SHA=<sha> MAIN_SHA=<sha> \
    scripts/ci/check-tools-manifest-staleness.sh

Environment:
  CANDIDATE_SHA   Commit the candidate image was built from, possibly
                  abbreviated. When empty the guard is skipped (callers that
                  promote non-tools images).
  CANDIDATE_PR    Pull request the candidate was built from. Used to fetch
                  refs/pull/<n>/head so an abbreviated CANDIDATE_SHA resolves
                  after the candidate branch is deleted.
  MAIN_SHA        Current main commit being promoted onto (required when
                  CANDIDATE_SHA is set).
  FORCE_PUBLISH   When "true", skip the guard and note it in the summary.
  MANIFEST_PATHS  Optional newline/whitespace-separated path override for the
                  manifest inputs compared between the two commits.
  GIT_REMOTE      Remote used to fetch a candidate commit that is not present
                  locally (default: origin).
  GITHUB_STEP_SUMMARY  When set, the refusal message is appended to it.

Exit codes:
  0  candidate manifest inputs match main (or the guard was skipped)
  1  main has newer tool manifest commits, or staleness cannot be determined
  2  usage error
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

# Manifest inputs the image-vs-manifest gate compares: the pinned tool
# versions and the hand-authored manifest source it renders
# lintro/tools/manifest.json from, the package map behind those tools, and the
# Dockerfile that bakes them into the image.
DEFAULT_MANIFEST_PATHS='
lintro/_tool_versions.py
lintro/tools/manifest.src.json
lintro/_tool_packages.py
docker/tools.Dockerfile
'

candidate_sha="${CANDIDATE_SHA:-}"
candidate_pr="${CANDIDATE_PR:-}"
main_sha="${MAIN_SHA:-}"
force_publish="${FORCE_PUBLISH:-}"
git_remote="${GIT_REMOTE:-origin}"
candidate_fetch_ref="${CANDIDATE_FETCH_REF:-refs/lintro/tools-candidate}"

summary() {
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		printf '%s\n' "$1" >>"$GITHUB_STEP_SUMMARY"
	fi
}

if [[ "$force_publish" == "true" ]]; then
	message="Manifest staleness guard skipped: force_publish=true."
	echo "$message"
	summary "$message"
	exit 0
fi

if [[ -z "$candidate_sha" ]]; then
	echo "Manifest staleness guard skipped: no CANDIDATE_SHA provided."
	exit 0
fi

if [[ -z "$main_sha" ]]; then
	echo "MAIN_SHA is required when CANDIDATE_SHA is set" >&2
	exit 2
fi

read -r -a manifest_paths <<<"$(echo "${MANIFEST_PATHS:-$DEFAULT_MANIFEST_PATHS}" | tr '\n' ' ')"

if [[ ${#manifest_paths[@]} -eq 0 ]]; then
	echo "MANIFEST_PATHS did not contain any paths" >&2
	exit 2
fi

fail_closed() {
	local message="$1"
	echo "$message" >&2
	summary "$message"
	exit 1
}

# resolve_commit <rev>
#
# Echo the full commit SHA for <rev>, or return 1 (unknown) / 2 (ambiguous
# abbreviation). The candidate SHA arrives abbreviated to 12 characters (the
# candidate tag embeds `sha[:12]`), so resolution has to go through rev-parse
# rather than a bare object-existence check.
resolve_commit() {
	local rev="$1" err out status
	err="$(mktemp)"
	if out="$(git rev-parse --verify "${rev}^{commit}" 2>"$err")"; then
		rm -f "$err"
		printf '%s' "$out"
		return 0
	fi
	status=1
	grep -qi 'ambiguous' "$err" && status=2
	rm -f "$err"
	return "$status"
}

# The candidate branch is deleted on merge, so its head commit is usually not
# in the checkout's objects. It stays reachable on GitHub as
# refs/pull/<n>/head, which is what the guard fetches: fetching by the
# abbreviated SHA the candidate tag carries is not possible (git can only
# fetch a full object id), so the PR number is the way in.
if ! candidate_full="$(resolve_commit "$candidate_sha")"; then
	if [[ -n "$candidate_pr" ]]; then
		git fetch --no-tags --quiet "$git_remote" \
			"refs/pull/${candidate_pr}/head:${candidate_fetch_ref}" 2>/dev/null || true
	elif [[ "$candidate_sha" =~ ^[0-9a-f]{40}$ ]]; then
		# A full object id can be fetched directly; no PR number needed.
		git fetch --no-tags --quiet "$git_remote" "$candidate_sha" 2>/dev/null || true
	fi
	candidate_full="$(resolve_commit "$candidate_sha")" || case $? in
	2)
		fail_closed "refusing to promote: candidate commit ${candidate_sha} is an ambiguous abbreviation in this checkout; rebuild from main with force_publish=true"
		;;
	*)
		fail_closed "refusing to promote: candidate commit ${candidate_sha} is not available in this checkout${candidate_pr:+ (fetched refs/pull/${candidate_pr}/head)}; rebuild from main with force_publish=true"
		;;
	esac
fi

# rev-parse already guarantees the prefix, but the candidate tag and the
# fetched PR head are independent inputs: assert they agree rather than
# comparing manifests against some other commit.
if [[ "$candidate_full" != "$candidate_sha"* ]]; then
	fail_closed "refusing to promote: candidate commit ${candidate_sha} resolved to ${candidate_full}; rebuild from main with force_publish=true"
fi

if ! main_full="$(resolve_commit "$main_sha")"; then
	fail_closed "refusing to promote: main commit ${main_sha} is not available in this checkout; rebuild from main with force_publish=true"
fi

changed_paths="$(git diff --name-only "$candidate_full" "$main_full" -- "${manifest_paths[@]}")"

if [[ -z "$changed_paths" ]]; then
	echo "Manifest inputs at ${main_full} match candidate commit ${candidate_full}."
	exit 0
fi

commits="$(git log --format=%H "${candidate_full}..${main_full}" -- "${manifest_paths[@]}" | tr '\n' ' ')"
commits="${commits% }"

if [[ -n "$commits" ]]; then
	message="refusing to promote: main has newer tool manifest commits ${commits}; rebuild from main with force_publish=true"
else
	# No manifest-touching commit sits between the two, yet the content
	# differs: the candidate branch itself diverged (an amended or rebased
	# manifest change that never landed on main in that form).
	message="refusing to promote: tool manifest inputs differ between candidate commit ${candidate_full} and main ${main_full}, but no manifest-touching commit lies between them (candidate-side divergence); rebuild from main with force_publish=true"
fi

echo "$message" >&2
echo "Changed manifest paths: $(echo "$changed_paths" | tr '\n' ' ')" >&2
summary "$message"
exit 1
