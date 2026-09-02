#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# dogfood-skip-gate.sh
#
# No-silent-skip gate (issue #1510). Consumes the authoritative JSON report
# from the dogfooding lint job when one is supplied, then runs
# scripts/ci/check-dogfood-skips.py to fail when an enabled tool silently skips
# for a reason not covered by the committed allowlist.
#
# The full-repo dogfooding jobs use an external reusable workflow (no place to
# add a post-lint step), which publishes `linting-json-report`. The workflow
# gate downloads that artifact and supplies REPORT_JSON, avoiding a second
# full-repo lint. When REPORT_JSON is unset, this script retains its historical
# derive-in-container path for changed-files PRs and dogfood-nightly. Only the
# `skipped` state matters here — real lint issues are gated by the dogfooding
# job itself, so lintro's own exit code is intentionally ignored.
#
# The same report is also classified for tool-execution timeouts (#1653) via
# scripts/ci/classify-lint-timeout.py, and the verdict is published as the
# `timeout-flake` / `timed-out-tools` job outputs. These are DIAGNOSTIC ONLY:
# this job always lints the full repo, so its verdict is not evidence about the
# authoritative lint run (which may be changed-files scoped, and is a separate
# run with its own timing). The code-quality gate deliberately does not consume
# them — doing so can green a genuine finding. See lgtm-ci#746.
#
# Usage:
#   LINTRO_IMAGE=ghcr.io/lgtm-hq/py-lintro:ci-123 scripts/ci/dogfood-skip-gate.sh
#   LINTRO_IMAGE=ghcr.io/lgtm-hq/py-lintro:ci-123 \
#   REPORT_JSON=.lintro/artifacts/json/results.json \
#   scripts/ci/dogfood-skip-gate.sh
#
# Environment:
#   LINTRO_IMAGE   Required. Pinned py-lintro image (CI tag or digest), used
#                  for report validation even when REPORT_JSON is supplied.
#   TOOL_OPTIONS   Optional. lintro --tool-options string (match the dogfood
#                  run so tool coverage — and thus skip behaviour — is identical).
#   ALLOWLIST      Optional. Allowlist path (default:
#                  scripts/ci/dogfood-skip-allowlist.yaml).
#   REPORT_JSON    Optional. Existing JSON report to consume. When set, the
#                  container lint is skipped. When unset, the report is
#                  written to dogfood-skip-report.json.
#   MAP_HOST_USER  Optional. true maps host UID/GID into the container
#                  (default: true on GitHub Actions).

show_help() {
	cat <<'EOF'
Usage:
  LINTRO_IMAGE=<image> scripts/ci/dogfood-skip-gate.sh
  LINTRO_IMAGE=<image> REPORT_JSON=<existing-report> scripts/ci/dogfood-skip-gate.sh

Run lintro chk (JSON) in Docker and fail on non-allowlisted tool skips.
Also publishes timeout-flake / timed-out-tools to GITHUB_OUTPUT (#1653).

Environment:
  LINTRO_IMAGE   Required. Pinned py-lintro image (CI tag or digest).
  TOOL_OPTIONS   Optional. lintro --tool-options string.
  ALLOWLIST      Optional. Allowlist YAML (default: scripts/ci/dogfood-skip-allowlist.yaml).
  REPORT_JSON    Optional. Existing JSON report to consume. When unset, lintro
                 derives a report in Docker at dogfood-skip-report.json.
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
: "${MAP_HOST_USER:=}"
if [[ -z "${MAP_HOST_USER}" ]] && [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
	MAP_HOST_USER=true
fi

log_info() { echo "[INFO] $*"; }
log_error() { echo "[ERROR] $*" >&2; }

report_from_artifact=false
if [[ -n "${REPORT_JSON:-}" ]]; then
	report_from_artifact=true
	if [[ ! -s "$REPORT_JSON" ]]; then
		log_error "REPORT_JSON does not point to a readable report: ${REPORT_JSON}"
		exit 2
	fi
	log_info "Using pre-existing lint report: ${REPORT_JSON}"
else
	REPORT_JSON=dogfood-skip-report.json
fi

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
lintro_args+=(--output-format json --output "$REPORT_JSON")

if [[ "$report_from_artifact" != true ]]; then
	# lintro exits non-zero when it finds real issues; the gate only cares about
	# skips, so ask lintro to write the machine-readable report directly. Stdout
	# can contain operational messages from the CLI and must not be parsed as
	# JSON.
	log_info "Running lintro check (JSON) in container to derive skip state..."
	rm -f "$REPORT_JSON" "${REPORT_JSON}.stdout"
	set +e
	"${docker_args[@]}" "${LINTRO_IMAGE}" "${lintro_args[@]}" >"${REPORT_JSON}.stdout"
	lintro_exit_code=$?
	set -e
	log_info "lintro exited ${lintro_exit_code} (ignored; gate checks skips only)"

	if [[ ! -s "$REPORT_JSON" ]]; then
		log_error "lintro produced no JSON report at ${REPORT_JSON}; cannot gate skips"
		exit 2
	fi
fi

# Classify tool-execution timeouts before the skip check so the outputs are
# published even when the skip gate itself fails (#1653). Stdlib-only, so it
# runs on the host rather than in the container. Never fails the gate: a
# non-classifiable report simply yields timeout-flake=false (fail closed).
log_info "Classifying tool-execution timeouts..."
set +e
python3 "$(dirname "${BASH_SOURCE[0]}")/classify-lint-timeout.py" \
	--report "${REPORT_JSON}"
classify_exit_code=$?
set -e
if [[ "${classify_exit_code}" -ne 0 ]]; then
	log_error "timeout classification failed (exit ${classify_exit_code}); continuing"
fi

# Run the checker inside the image: it ships PyYAML, so no host Python deps are
# needed. The workspace is mounted at /code, so the report and allowlist are
# both visible there.
log_info "Checking skips against ${ALLOWLIST}..."
report_in_container="/code/${REPORT_JSON}"
if [[ "$REPORT_JSON" == /* ]]; then
	workspace_dir="$(pwd -P)"
	case "$REPORT_JSON" in
		"${workspace_dir}"/*)
			report_in_container="/code/${REPORT_JSON#"${workspace_dir}/"}"
			;;
		*)
			log_error "REPORT_JSON must be inside the mounted workspace: ${REPORT_JSON}"
			exit 2
			;;
	esac
fi
"${docker_args[@]}" --entrypoint python3 "${LINTRO_IMAGE}" \
	/code/scripts/ci/check-dogfood-skips.py \
	--report "$report_in_container" \
	--allowlist "/code/${ALLOWLIST}"
