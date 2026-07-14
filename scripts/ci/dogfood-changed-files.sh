#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# dogfood-changed-files.sh
#
# Run lintro chk inside the pinned py-lintro Docker image against only the
# files changed on a pull request (git diff against the merge-base with the
# PR base branch). Mirrors lgtm-ci run-lintro-docker.sh — same image, same
# full tool set (no --tools narrowing), same --tool-options, same docker
# invocation — so changed-files mode never diverges in tool coverage from
# the full-repo run; only the path arguments differ.
#
# Fail-safe: any trouble resolving the diff (missing base ref, merge-base
# failure) or an oversized change set falls back to full-repo lint (`.`)
# rather than skipping files. An empty change set (e.g. deletions only)
# passes without invoking lintro — there is nothing left to lint.
#
# Usage:
#   LINTRO_IMAGE=ghcr.io/lgtm-hq/py-lintro:ci-123 BASE_REF=main \
#     scripts/ci/dogfood-changed-files.sh

show_help() {
	cat <<'EOF'
Usage:
  LINTRO_IMAGE=<image> BASE_REF=<branch> scripts/ci/dogfood-changed-files.sh

Run lintro chk in Docker against only the files changed vs the PR base.

Environment:
  LINTRO_IMAGE       Required. Pinned py-lintro image (CI tag or digest).
  BASE_REF           Required. PR base branch (github.base_ref), e.g. main.
  TOOL_OPTIONS       Optional. lintro --tool-options string (must match the
                     full-repo run so coverage is identical).
  OUTPUT_LOG         Optional. chk output log path (default: chk-output.txt).
  MAX_CHANGED_FILES  Optional. Above this many changed files fall back to
                     full-repo lint (default: 300).
  MAP_HOST_USER      Optional. true maps host UID/GID into the container
                     (default: true on GitHub Actions).

Behavior:
  - Diffs merge-base(origin/BASE_REF, HEAD)..HEAD with --diff-filter=ACMR
    and keeps only paths that still exist on disk.
  - Unresolvable base/merge-base or > MAX_CHANGED_FILES changed files fall
    back to full-repo lint ('.').
  - No surviving changed files: passes without running lintro.

Outputs (via GITHUB_OUTPUT):
  exit-code=<lintro exit code>
  status=passed|failed
  lint-mode=changed-files|full-fallback|empty
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

: "${LINTRO_IMAGE:?LINTRO_IMAGE is required}"
: "${BASE_REF:?BASE_REF is required (PR base branch, e.g. main)}"
: "${TOOL_OPTIONS:=}"
: "${OUTPUT_LOG:=chk-output.txt}"
: "${MAX_CHANGED_FILES:=300}"
: "${MAP_HOST_USER:=}"
if [[ -z "${MAP_HOST_USER}" ]] && [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
	MAP_HOST_USER=true
fi

log_info() { echo "[INFO] $*"; }
log_error() { echo "[ERROR] $*" >&2; }

write_outputs() {
	local exit_code="$1"
	local lint_mode="$2"
	local status="passed"
	if [[ "$exit_code" -ne 0 ]]; then
		status="failed"
	fi
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		{
			echo "exit-code=${exit_code}"
			echo "status=${status}"
			echo "lint-mode=${lint_mode}"
		} >>"$GITHUB_OUTPUT"
	fi
	echo "exit-code=${exit_code} status=${status} lint-mode=${lint_mode}"
}

# Resolve the base ref: prefer the remote-tracking ref (CI checkouts), fall
# back to a local branch (local runs and tests).
base_commit=""
if git rev-parse --verify --quiet "origin/${BASE_REF}^{commit}" >/dev/null; then
	base_commit="origin/${BASE_REF}"
elif git rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null; then
	base_commit="${BASE_REF}"
fi

lint_mode="changed-files"
declare -a lint_paths=()

if [[ -z "$base_commit" ]]; then
	log_error "Base ref '${BASE_REF}' not resolvable; falling back to" \
		"full-repo lint"
	lint_mode="full-fallback"
elif ! merge_base="$(git merge-base "$base_commit" HEAD 2>/dev/null)"; then
	log_error "merge-base(${base_commit}, HEAD) failed; falling back to" \
		"full-repo lint"
	lint_mode="full-fallback"
else
	log_info "Diffing ${merge_base}..HEAD (merge-base of ${base_commit})"
	# ACMR excludes deletions; rename sources never appear. The existence
	# check below drops anything that vanished since the diff was taken.
	while IFS= read -r -d '' changed_file; do
		if [[ -e "$changed_file" ]]; then
			lint_paths+=("$changed_file")
		fi
	done < <(
		git diff --name-only -z --diff-filter=ACMR "$merge_base" HEAD
	)
	if [[ "${#lint_paths[@]}" -gt "$MAX_CHANGED_FILES" ]]; then
		log_info "${#lint_paths[@]} changed files exceed the" \
			"MAX_CHANGED_FILES=${MAX_CHANGED_FILES} cap; falling back to" \
			"full-repo lint"
		lint_mode="full-fallback"
		lint_paths=()
	fi
fi

if [[ "$lint_mode" == "full-fallback" ]]; then
	lint_paths=(.)
elif [[ "${#lint_paths[@]}" -eq 0 ]]; then
	log_info "No changed files survive the diff filter; nothing to lint"
	write_outputs 0 "empty"
	exit 0
else
	log_info "Linting ${#lint_paths[@]} changed file(s):"
	printf '  %s\n' "${lint_paths[@]}"
fi

# Pull explicitly so a registry failure still honors the output contract:
# downstream jobs (PR comment, required-check gate) key off exit-code/status
# and must see an explicit failure, not empty outputs.
log_info "Pulling Lintro image: ${LINTRO_IMAGE}"
set +e
docker pull "$LINTRO_IMAGE"
pull_exit_code=$?
set -e
if [[ "$pull_exit_code" -ne 0 ]]; then
	log_error "Failed to pull Lintro image ${LINTRO_IMAGE}" \
		"(exit ${pull_exit_code})"
	write_outputs "$pull_exit_code" "$lint_mode"
	exit "$pull_exit_code"
fi

# Same container invocation as lgtm-ci run-lintro-docker.sh (quality lint):
# host-UID mapping keeps the workspace mount writable on GitHub Actions.
declare -a docker_args=(
	docker run --rm
	-e HOME=/tmp
	-e LINTRO_AUTO_INSTALL_DEPS=1
	-v "$(pwd):/code"
	-w /code
)
if [[ "$MAP_HOST_USER" == "true" ]]; then
	docker_args+=(--user "$(id -u):$(id -g)")
fi
docker_args+=("$LINTRO_IMAGE")

declare -a lintro_args=(chk)
if [[ -n "$TOOL_OPTIONS" ]]; then
	lintro_args+=(--tool-options "$TOOL_OPTIONS")
fi
lintro_args+=("${lint_paths[@]}" --output-format grid)

log_info "Running lintro check in container (${lint_mode})..."
set +e
set -o pipefail
"${docker_args[@]}" "${lintro_args[@]}" 2>&1 | tee "$OUTPUT_LOG"
lintro_exit_code="${PIPESTATUS[0]}"
set +o pipefail
set -e

write_outputs "$lintro_exit_code" "$lint_mode"

if [[ "$lintro_exit_code" -ne 0 ]]; then
	log_error "Lintro check failed with exit code ${lintro_exit_code}"
	exit "$lintro_exit_code"
fi
log_info "Lintro check completed successfully"
