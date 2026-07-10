#!/usr/bin/env bash
# Build the docs site and optionally merge local coverage HTML for a Pages-like preview.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SITE_DIR="${ROOT}/apps/site"
DIST_DIR="${SITE_DIR}/dist"

INCLUDE_RUST="${PREVIEW_INCLUDE_RUST:-1}"
INCLUDE_WEB="${PREVIEW_INCLUDE_WEB:-1}"
SKIP_BUILD="${PREVIEW_SKIP_BUILD:-0}"

usage() {
	cat <<'EOF'
Usage: ./scripts/ci/site/preview-pages-local.sh

Builds apps/site/dist with production ASTRO_BASE (/Rustume/) and copies local
coverage HTML into the same paths used on GitHub Pages (coverage-rust/, coverage-web/).

Environment:
  PREVIEW_SKIP_BUILD=1     Skip ./scripts/ci/site/build.sh
  PREVIEW_INCLUDE_RUST=0   Skip Rust coverage HTML generation
  PREVIEW_INCLUDE_WEB=0    Skip web Vitest coverage
  PREVIEW_SERVE=0          Build only; do not start astro preview

After the script runs, open the URL printed by "astro preview" (default base /Rustume/).
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	usage
	exit 0
fi

cd "${ROOT}"

if [[ "${SKIP_BUILD}" != "1" ]]; then
	echo "==> Building documentation site (ASTRO_BASE from defaults.env)"
	./scripts/ci/site/build.sh
fi

if [[ ! -d "${DIST_DIR}" ]]; then
	echo "Missing ${DIST_DIR}; run build first." >&2
	exit 1
fi

copy_tree() {
	local src="$1"
	local dest="$2"
	if [[ ! -d "${src}" ]]; then
		echo "Skip copy: ${src} does not exist"
		return 0
	fi
	rm -rf "${dest}"
	mkdir -p "$(dirname "${dest}")"
	cp -a "${src}" "${dest}"
	echo "Copied ${src} -> ${dest}"
}

if [[ "${INCLUDE_WEB}" == "1" ]]; then
	echo "==> Running web Vitest coverage (apps/web)"
	(
		cd "${ROOT}/apps/web"
		bun install --frozen-lockfile
		bun run test:coverage
	)
	copy_tree "${ROOT}/apps/web/coverage" "${DIST_DIR}/coverage-web"
fi

if [[ "${INCLUDE_RUST}" == "1" ]]; then
	echo "==> Running Rust coverage + HTML (workspace; may take several minutes)"
	./scripts/ci/testing/ci-setup-rust-coverage.sh
	./scripts/ci/testing/ci-rust-coverage.sh
	cargo llvm-cov report --html --output-dir rust-coverage-html
	copy_tree "${ROOT}/rust-coverage-html" "${DIST_DIR}/coverage-rust"
fi

echo ""
echo "Pages-like dist ready at: ${DIST_DIR}"
echo "  Docs:     /Rustume/"
echo "  Rust cov: /Rustume/coverage-rust/"
echo "  Web cov:  /Rustume/coverage-web/"
echo ""

if [[ "${PREVIEW_SERVE:-1}" == "0" ]]; then
	exit 0
fi

echo "==> Starting astro preview (Ctrl+C to stop)"
exec "${SCRIPT_DIR}/preview-serve.sh" --host 127.0.0.1
