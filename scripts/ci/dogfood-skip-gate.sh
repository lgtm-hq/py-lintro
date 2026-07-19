#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# dogfood-skip-gate.sh
#
# No-silent-skip gate (issue #1510). Runs lintro chk inside the pinned
# py-lintro Docker image with `--output-format json` over the whole repo,
# then runs scripts/ci/check-dogfood-skips.py to fail when an enabled tool
# silently skips for a reason not covered by the committed allowlist.
#
# This runs AFTER the dogfooding lint job as a dedicated gate: the dogfood
# jobs use an external reusable workflow (no place to add a step), so the gate
# re-derives the structured skip state from the same image. Only the `skipped`
# state matters here — real lint issues are gated by the dogfood job itself, so
# lintro's own exit code is intentionally ignored.
#
# Usage:
#   LINTRO_IMAGE=ghcr.io/lgtm-hq/py-lintro:ci-123 scripts/ci/dogfood-skip-gate.sh
#
# Environment:
#   LINTRO_IMAGE   Required. Pinned py-lintro image (CI tag or digest).
#   TOOL_OPTIONS   Optional. lintro --tool-options string (match the dogfood
#                  run so tool coverage — and thus skip behaviour — is identical).
#   ALLOWLIST      Optional. Allowlist path (default:
#                  scripts/ci/dogfood-skip-allowlist.yaml).
#   REPORT_JSON    Optional. Where to write the JSON report (default:
#                  dogfood-skip-report.json).
#   MAP_HOST_USER  Optional. true maps host UID/GID into the container
#                  (default: true on GitHub Actions).

show_help() {
	cat <<'EOF'
Usage:
  LINTRO_IMAGE=<image> scripts/ci/dogfood-skip-gate.sh

Run lintro chk (JSON) in Docker and fail on non-allowlisted tool skips.

Environment:
  LINTRO_IMAGE   Required. Pinned py-lintro image (CI tag or digest).
  TOOL_OPTIONS   Optional. lintro --tool-options string.
  ALLOWLIST      Optional. Allowlist YAML (default: scripts/ci/dogfood-skip-allowlist.yaml).
  REPORT_JSON    Optional. JSON report output path (default: dogfood-skip-report.json).
  MAP_HOST_USER  Optional. true maps host UID/GID into the container.

Exit codes:
  0  no non-allowlisted skips
  1  one or more non-allowlisted skips
  2  usage / configuration error, or the lintro run produced no JSON
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

: "${LINTRO_IMAGE:?LINTRO_IMAGE is required}"
: "${TOOL_OPTIONS:=}"
: "${ALLOWLIST:=scripts/ci/dogfood-skip-allowlist.yaml}"
: "${REPORT_JSON:=dogfood-skip-report.json}"
: "${MAP_HOST_USER:=}"
if [[ -z "${MAP_HOST_USER}" ]] && [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
	MAP_HOST_USER=true
fi

log_info() { echo "[INFO] $*"; }
log_error() { echo "[ERROR] $*" >&2; }

# Pull explicitly so a registry failure surfaces as a clear gate error rather
# than an empty report.
log_info "Pulling Lintro image: ${LINTRO_IMAGE}"
set +e
docker pull "$LINTRO_IMAGE"
pull_exit_code=$?
set -e
if [[ "$pull_exit_code" -ne 0 ]]; then
	log_error "Failed to pull Lintro image ${LINTRO_IMAGE} (exit ${pull_exit_code})"
	exit 2
fi

# Same container invocation as the dogfood run (host-UID mapping keeps the
# workspace mount writable on GitHub Actions).
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

declare -a lintro_args=(chk .)
if [[ -n "$TOOL_OPTIONS" ]]; then
	lintro_args+=(--tool-options "$TOOL_OPTIONS")
fi
lintro_args+=(--output-format json)

# lintro exits non-zero when it finds real issues; the gate only cares about
# skips, so capture stdout (pure JSON for machine formats) and ignore the exit
# code. Warnings go to stderr and are streamed to the log.
log_info "Running lintro check (JSON) in container to derive skip state..."
set +e
"${docker_args[@]}" "${LINTRO_IMAGE}" "${lintro_args[@]}" >"$REPORT_JSON"
lintro_exit_code=$?
set -e
log_info "lintro exited ${lintro_exit_code} (ignored; gate checks skips only)"

if [[ ! -s "$REPORT_JSON" ]]; then
	log_error "lintro produced no JSON report at ${REPORT_JSON}; cannot gate skips"
	exit 2
fi

# Run the checker inside the image: it ships PyYAML, so no host Python deps are
# needed. The workspace is mounted at /code, so the report and allowlist are
# both visible there.
log_info "Checking skips against ${ALLOWLIST}..."
"${docker_args[@]}" --entrypoint python3 "${LINTRO_IMAGE}" \
	/code/scripts/ci/check-dogfood-skips.py \
	--report "/code/${REPORT_JSON}" \
	--allowlist "/code/${ALLOWLIST}"
