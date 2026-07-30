#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# release-bump-only.sh
#
# Decide whether a pull request is an automated release version-bump PR so
# the heavy Docker pipeline can be skipped for it (#1362). Two layers:
#
#   1. Nomination (identity signals — spoofable, so they only NOMINATE):
#      PR author is the release bot, title matches
#      `chore(release): version X.Y.Z`, and the head branch is
#      `release/vX.Y.Z` (what reusable-release-version-pr produces).
#   2. Verification (diff allowlist — DECIDES): the diff vs the PR base must
#      touch only CHANGELOG.md, pyproject.toml, uv.lock,
#      <package>/__init__.py, SECURITY.md, and .github/SECURITY.md, and
#      outside CHANGELOG.md the only permitted change is the project's own
#      version stamp:
#        - pyproject.toml: only the [project] `version = "..."` line;
#        - <package>/__init__.py: only the `__version__ = "..."` line;
#        - uv.lock: only the `version = "..."` line of the project's own
#          [[package]] block. A bare version-line diff check would be
#          spoofable by a dependency bump whose diff also only touches
#          version lines, so each file is compared with its version stamp
#          stripped from BOTH revisions — the remainders must be
#          byte-identical (prior art: lgtm-hq/Rustume
#          scripts/ci/docker/release_bump_only.sh, #457).
#        - SECURITY.md / .github/SECURITY.md: only the supported-versions
#          table rows (the `major.minor.x` supported row and the
#          `< major.minor` unsupported row) may change, so minor/major bumps
#          — which the version PR now stamps into that table (#1372) — stay
#          bump-only. Any other SECURITY.md edit survives the row strip and
#          fails the byte-identical remainder check (#1362 content guard).
#
# CHANGELOG.md content is not inspected (prose never affects the image or
# the test matrix). Every unexpected condition fails closed to "false" so
# the full pipeline runs.
#
# Usage (from the docker-ci `changes` job, pull_request events only):
#   EVENT_NAME=pull_request PR_AUTHOR='lgtm-release-bot[bot]' \
#     PR_TITLE='chore(release): version 1.2.3' HEAD_REF=release/v1.2.3 \
#     scripts/ci/release-bump-only.sh
#
# The diff range defaults to HEAD^1..HEAD, which on the pull_request merge
# ref is exactly the PR's effect against its base; a non-merge HEAD fails
# closed. BASE_SHA/HEAD_SHA override the range (tests).
#
# Outputs (via GITHUB_OUTPUT, also echoed):
#   release-bump=true|false

show_help() {
	cat <<'EOF'
Classify a pull request as an automated release version-bump PR.

Usage:
  EVENT_NAME=pull_request PR_AUTHOR=<login> PR_TITLE=<title> \
    HEAD_REF=<branch> scripts/ci/release-bump-only.sh

Environment:
  EVENT_NAME    github.event_name; anything but pull_request resolves false
  PR_AUTHOR     github.event.pull_request.user.login
  PR_TITLE      github.event.pull_request.title
  HEAD_REF      github.head_ref (PR head branch name)
  RELEASE_BOT   Release bot login (default: lgtm-release-bot[bot])
  PACKAGE_NAME  Python package directory name (default: lintro)
  BASE_SHA      Diff base override (default: HEAD^1 of the merge ref)
  HEAD_SHA      Diff head override (default: HEAD)

Behavior:
  - Identity signals (bot author + chore(release) title + release/v*
    branch) only nominate; the diff allowlist decides.
  - Qualifying diffs touch only CHANGELOG.md, pyproject.toml, uv.lock,
    <package>/__init__.py, SECURITY.md, and .github/SECURITY.md, and
    change nothing beyond the project's own version stamp in pyproject/
    uv.lock/__init__ and the supported-versions table rows in SECURITY.md.
  - Any unexpected condition fails closed to release-bump=false.

Outputs (via GITHUB_OUTPUT):
  release-bump=true|false
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

event_name="${EVENT_NAME:-}"
pr_author="${PR_AUTHOR:-}"
pr_title="${PR_TITLE:-}"
head_ref="${HEAD_REF:-}"
release_bot="${RELEASE_BOT:-lgtm-release-bot[bot]}"
package_name="${PACKAGE_NAME:-lintro}"
init_file="${package_name}/__init__.py"

emit() {
	local value="$1"
	echo "release-bump=${value}"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		echo "release-bump=${value}" >>"$GITHUB_OUTPUT"
	fi
	exit 0
}

# --- Layer 1: nomination (identity signals) --------------------------------

if [[ "$event_name" != "pull_request" ]]; then
	echo "not a pull_request event (${event_name:-<unset>})"
	emit "false"
fi

semver='[0-9]+\.[0-9]+\.[0-9]+'
if [[ "$pr_author" != "$release_bot" ]]; then
	echo "not nominated: author '${pr_author}' is not '${release_bot}'"
	emit "false"
fi
if [[ ! "$pr_title" =~ ^chore\(release\):\ version\ ${semver}$ ]]; then
	echo "not nominated: title '${pr_title}' is not a version-bump title"
	emit "false"
fi
if [[ ! "$head_ref" =~ ^release/v${semver}$ ]]; then
	echo "not nominated: head ref '${head_ref}' is not release/vX.Y.Z"
	emit "false"
fi

# --- Diff range -------------------------------------------------------------

base="${BASE_SHA:-}"
head="${HEAD_SHA:-}"
if [[ -z "$base" || -z "$head" ]]; then
	# pull_request checkouts sit on the synthetic merge commit; its first
	# parent is the base branch tip, so HEAD^1..HEAD is exactly the change
	# the merge would land. A non-merge HEAD means we cannot trust the
	# range — fail closed.
	if ! git rev-parse --verify --quiet 'HEAD^2' >/dev/null; then
		echo "HEAD is not a PR merge commit; failing closed" >&2
		emit "false"
	fi
	base="$(git rev-parse 'HEAD^1')"
	head="$(git rev-parse HEAD)"
fi

# --- Layer 2: verification (diff allowlist decides) -------------------------

allowed_files=(
	"CHANGELOG.md"
	"pyproject.toml"
	"uv.lock"
	"$init_file"
	"SECURITY.md"
	".github/SECURITY.md"
)

changed_files="$(git diff --name-only "$base" "$head")"

if [[ -z "$changed_files" ]]; then
	echo "not bump-only: empty diff"
	emit "false"
fi

while IFS= read -r f; do
	ok=false
	for a in "${allowed_files[@]}"; do
		if [[ "$f" == "$a" ]]; then
			ok=true
			break
		fi
	done
	if [[ "$ok" == "false" ]]; then
		echo "not bump-only: ${f} outside allowlist"
		emit "false"
	fi
done <<<"$changed_files"

# pyproject.toml: a bare version-line check is section-blind — a
# `version = "..."` line under a dependency table looks identical to the
# project's own stamp. Strip the version line from the [project] section of
# both revisions and require the remainders to be byte-identical.
strip_project_version() {
	git show "$1:pyproject.toml" 2>/dev/null | awk '
		/^\[/ { in_project = ($0 == "[project]") }
		!(in_project && /^version = "[0-9]+\.[0-9]+\.[0-9]+[^"]*"$/)
	'
}

if ! git diff --quiet "$base" "$head" -- pyproject.toml; then
	if ! diff -q <(strip_project_version "$base") \
		<(strip_project_version "$head") >/dev/null; then
		echo "not bump-only: pyproject.toml changed beyond [project] version"
		emit "false"
	fi
fi

# <package>/__init__.py: only the __version__ stamp may change.
strip_dunder_version() {
	git show "$1:${init_file}" 2>/dev/null |
		grep -v '^__version__ = "[0-9]\+\.[0-9]\+\.[0-9]\+[^"]*"$' || true
}

if ! git diff --quiet "$base" "$head" -- "$init_file"; then
	if ! diff -q <(strip_dunder_version "$base") \
		<(strip_dunder_version "$head") >/dev/null; then
		echo "not bump-only: ${init_file} changed beyond __version__"
		emit "false"
	fi
fi

# uv.lock: only the project's own [[package]] block may change, and only its
# version line. Strip that single line from both revisions and require the
# remainders to be byte-identical — any dependency entry change (version,
# hashes, additions, removals) survives the strip and fails the diff.
strip_lock_project_version() {
	git show "$1:uv.lock" 2>/dev/null | awk -v pkg="$2" '
		/^\[/ { own = 0 }
		$0 == "name = \"" pkg "\"" && in_block { own = 1 }
		/^\[\[package\]\]$/ { in_block = 1 }
		/^\[/ && !/^\[\[package\]\]$/ { in_block = 0 }
		!(own && /^version = "[0-9]+\.[0-9]+\.[0-9]+[^"]*"$/)
	'
}

if ! git diff --quiet "$base" "$head" -- uv.lock; then
	if ! diff -q <(strip_lock_project_version "$base" "$package_name") \
		<(strip_lock_project_version "$head" "$package_name") >/dev/null; then
		echo "not bump-only: uv.lock changed beyond the ${package_name} version"
		emit "false"
	fi
fi

# SECURITY.md / .github/SECURITY.md: only the supported-versions table rows may
# change. The version PR stamps the current `major.minor.x` supported row and
# the `< major.minor` unsupported row (#1372); strip exactly those row shapes —
# regardless of column padding or the support mark used (emoji or GitHub
# shortcode) — from both revisions and require the remainders to be
# byte-identical. Any other SECURITY.md edit (prose, a new row, a changed mark
# on a non-version row) survives the strip and fails closed.
strip_security_rows() {
	git show "$1:$2" 2>/dev/null | grep -Ev \
		'^\|[[:space:]]*([0-9]+\.[0-9]+\.x|<[[:space:]]*[0-9]+\.[0-9]+)[[:space:]]*\|.*\|[[:space:]]*$' ||
		true
}

for security_file in "SECURITY.md" ".github/SECURITY.md"; do
	if ! git diff --quiet "$base" "$head" -- "$security_file"; then
		if ! diff -q <(strip_security_rows "$base" "$security_file") \
			<(strip_security_rows "$head" "$security_file") >/dev/null; then
			echo "not bump-only: ${security_file} changed beyond the support table"
			emit "false"
		fi
	fi
done

echo "version-bump PR verified: diff limited to version stamp + CHANGELOG + SECURITY table"
emit "true"
