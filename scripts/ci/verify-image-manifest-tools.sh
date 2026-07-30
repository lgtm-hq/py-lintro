#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
set -euo pipefail

# verify-image-manifest-tools.sh
#
# Run scripts/ci/verify-manifest-tools.py *inside* a container IMAGE so the
# tools actually baked into the image are executed and checked against the
# CURRENT repository manifest. This turns "manifest declares a tool the image
# cannot execute" (e.g. the pip-audit digest-lag in #1505) into a hard CI
# failure instead of a silent dogfooding SKIP.
#
# Why mount the repo instead of using the manifest baked into the image:
#   - The pinned-digest path (fork PRs / dogfood-nightly) runs an OLDER release
#     image. Verifying that image against the CURRENT manifest is exactly how
#     digest lag surfaces: the current manifest lists a tool the stale image
#     lacks, so the check fails.
#   - The verifier and one tool's version_command (vue_tsc →
#     scripts/ci/resolve-vue-tsc-version.sh) live in scripts/, which is not
#     re-copied into the runtime image, so the checkout must be mounted to run
#     the current code path.
#
# The version_command subprocesses resolve their binaries from the image's own
# PATH (the entrypoint is bypassed with `python3` so the container's baked
# ENV — PATH, BUN_INSTALL, CARGO_HOME — is used as-is and no gosu redirect
# rewrites BUN_INSTALL out from under resolve-vue-tsc-version.sh).
#
# Usage:
#   IMAGE=ghcr.io/lgtm-hq/py-lintro:ci-123 \
#     scripts/ci/verify-image-manifest-tools.sh

show_help() {
	cat <<'EOF'
Usage:
  IMAGE=<image> scripts/ci/verify-image-manifest-tools.sh

Run verify-manifest-tools.py inside a container image, checking the image's
installed tools against the current repository manifest.

Environment:
  IMAGE          Required. Image ref to verify (CI tag, local tag, or @sha256
                 digest). Registry refs are auto-pulled by `docker run`.
  TIERS          Optional. Comma-separated manifest tiers to verify
                 (default: tools).
  MANIFEST       Optional. Manifest path relative to the repo root
                 (default: lintro/tools/manifest.json).
  BASE_REF       Optional. PR base branch (github.base_ref). When set, tools
                 the PR newly adds to the manifest are computed (diff vs the
                 merge-base) and passed as --allow-missing, so their absent
                 binary in the digest-pinned base image downgrades to a
                 warning instead of failing the gate (#1565). Tools whose
                 manifest *version* the PR bumps are likewise computed and
                 passed as --allow-version-lag (#1582). Unset on main/nightly
                 -> empty allowlists -> full enforcement.
  ALLOW_MISSING  Optional. Explicit comma-separated allow-missing tool names.
                 Overrides the BASE_REF-derived set (used by unit tests).
  ALLOW_VERSION_LAG  Optional. Explicit comma-separated allow-version-lag
                 tool names. Overrides the BASE_REF-derived set (unit tests).
  DRY_RUN        Optional. When "1"/"true", print the docker command and exit 0
                 without invoking docker (used by unit tests).

Exit codes:
  0  all verified tiers match the manifest
  1  a tool is missing or a version mismatches (manifest-vs-image drift)
  2  usage / manifest-load error
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

: "${IMAGE:?IMAGE is required (e.g. ghcr.io/lgtm-hq/py-lintro:ci-123)}"
: "${TIERS:=tools}"
: "${MANIFEST:=lintro/tools/manifest.json}"
: "${BASE_REF:=}"
: "${ALLOW_MISSING:=}"
: "${ALLOW_VERSION_LAG:=}"
: "${DRY_RUN:=}"

log_info() { echo "[INFO] $*"; }
log_error() { echo "[ERROR] $*" >&2; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve the repository root to mount: prefer git, fall back to the script's
# grandparent (scripts/ci/ -> repo root).
if repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
	:
else
	repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

if [[ ! -f "${repo_root}/${MANIFEST}" ]]; then
	log_error "Manifest not found: ${repo_root}/${MANIFEST}"
	exit 2
fi

# Compute allowlists (#1565 / #1582). Explicit ALLOW_* wins (tests); otherwise
# derive from BASE_REF via the fail-closed helper. On main/nightly (no
# BASE_REF) the helper returns empty -> full enforcement.
if [[ -z "$ALLOW_MISSING" ]]; then
	ALLOW_MISSING="$(BASE_REF="$BASE_REF" MANIFEST="$MANIFEST" EMIT=added \
		"${script_dir}/compute-new-manifest-tools.sh")"
fi
if [[ -z "$ALLOW_VERSION_LAG" ]]; then
	ALLOW_VERSION_LAG="$(
		BASE_REF="$BASE_REF" MANIFEST="$MANIFEST" EMIT=version-changed \
			"${script_dir}/compute-new-manifest-tools.sh"
	)"
fi

# Bypass the image entrypoint so the container's baked ENV is used verbatim and
# the checkout is mounted read-only outside /code (the entrypoint's gosu path).
declare -a docker_args=(
	docker run --rm
	--entrypoint python3
	-v "${repo_root}:/repo:ro"
	-w /repo
	"$IMAGE"
	scripts/ci/verify-manifest-tools.py
	--manifest "$MANIFEST"
	--tiers "$TIERS"
)
if [[ -n "$ALLOW_MISSING" ]]; then
	docker_args+=(--allow-missing "$ALLOW_MISSING")
	log_info "Tolerating newly-added tool(s): ${ALLOW_MISSING}"
fi
if [[ -n "$ALLOW_VERSION_LAG" ]]; then
	docker_args+=(--allow-version-lag "$ALLOW_VERSION_LAG")
	log_info "Tolerating version-lag tool(s): ${ALLOW_VERSION_LAG}"
fi

if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" ]]; then
	log_info "[DRY-RUN] ${docker_args[*]}"
	exit 0
fi

log_info "Verifying image tools against manifest (tiers: ${TIERS})"
log_info "Image:    ${IMAGE}"
log_info "Manifest: ${MANIFEST}"
"${docker_args[@]}"
