#!/usr/bin/env bash
set -euo pipefail

# sign-dist-sigstore.sh
#
# Sign the Python distribution files (sdist + wheel) with Sigstore using
# keyless signing backed by the GitHub Actions OIDC token. One `.sigstore`
# bundle is produced per input artifact; each bundle embeds the Fulcio
# certificate and the Rekor transparency-log entry, so no long-lived key or
# secret is required (only `id-token: write` in the calling job).
#
# The resulting bundles are attached to the GitHub Release as assets, which
# satisfies the OpenSSF Scorecard Signed-Releases check and lets consumers run
# `sigstore verify identity` / `cosign verify-blob` against release artifacts.
#
# Usage:
#   DIST_DIR=<dir> scripts/ci/sign-dist-sigstore.sh
#
# Environment:
#   DIST_DIR   Directory containing the *.tar.gz and *.whl files (default: dist)

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Sign Python distribution files with Sigstore (keyless, GitHub OIDC).

Usage:
  DIST_DIR=<dir> scripts/ci/sign-dist-sigstore.sh

Environment:
  DIST_DIR   Directory containing the *.tar.gz and *.whl files (default: dist)

Produces one `<artifact>.sigstore` bundle per distribution file in DIST_DIR.
EOF
	exit 0
fi

DIST_DIR="${DIST_DIR:-dist}"

if [[ ! -d "${DIST_DIR}" ]]; then
	echo "sign-dist-sigstore: distribution directory not found: ${DIST_DIR}" >&2
	exit 1
fi

# Collect distribution artifacts (sdist + wheel). Fail loudly if none exist so a
# broken build cannot silently produce an unsigned release.
mapfile -t artifacts < <(find "${DIST_DIR}" -maxdepth 1 -type f \
	\( -name '*.tar.gz' -o -name '*.whl' \) | sort)

if [[ "${#artifacts[@]}" -eq 0 ]]; then
	echo "sign-dist-sigstore: no *.tar.gz or *.whl files found in ${DIST_DIR}" >&2
	exit 1
fi

echo "sign-dist-sigstore: signing ${#artifacts[@]} artifact(s) in ${DIST_DIR}"

for artifact in "${artifacts[@]}"; do
	bundle="${artifact}.sigstore"
	echo "sign-dist-sigstore: signing ${artifact} -> ${bundle}"
	# `--bundle` pins the output path so the extension is always `.sigstore`,
	# the pattern the Scorecard Signed-Releases check looks for. `--overwrite`
	# keeps re-runs idempotent.
	uvx --from sigstore sigstore sign \
		--bundle "${bundle}" \
		--overwrite \
		"${artifact}"
	if [[ ! -s "${bundle}" ]]; then
		echo "sign-dist-sigstore: expected bundle not created: ${bundle}" >&2
		exit 1
	fi
done

echo "sign-dist-sigstore: created bundles:"
find "${DIST_DIR}" -maxdepth 1 -type f -name '*.sigstore' | sort
