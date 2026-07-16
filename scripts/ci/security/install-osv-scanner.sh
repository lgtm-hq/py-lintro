#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# install-osv-scanner.sh — Download and verify osv-scanner with retries.
#
# Local override for py-lintro vuln-suppression-check (#1314). Uses lgtm-ci
# hardened curl helpers and retries transient curl failures (e.g. exit 23).
#
# Usage:
#   install-osv-scanner.sh [version]
#
# Environment:
#   OSV_VERSION            Release version (default: 2.3.5)
#   INSTALL_DIR            Install directory (default: /usr/local/bin or ~/.local/bin)
#   DOWNLOAD_MAX_ATTEMPTS  Retry count for downloads (default: 3)

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: install-osv-scanner.sh [version]

Download and verify the osv-scanner release binary with retries.

Version: $1 > $OSV_VERSION > 2.3.5
Install dir: $INSTALL_DIR > /usr/local/bin > ~/.local/bin
EOF
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
TOOLING_LIB="${REPO_ROOT}/.lgtm-ci-tooling/scripts/ci/lib"

if [[ ! -d "$TOOLING_LIB" ]]; then
	echo "[ERROR] lgtm-ci tooling not found at ${TOOLING_LIB}" >&2
	echo "[ERROR] Ensure checkout-and-harden runs before this script." >&2
	exit 1
fi

# shellcheck source=/dev/null
source "${TOOLING_LIB}/log.sh"
# shellcheck source=/dev/null
source "${TOOLING_LIB}/fs.sh"
# shellcheck source=/dev/null
source "${TOOLING_LIB}/network/download.sh"

OSV_VERSION="${1:-${OSV_VERSION:-2.3.5}}"
DOWNLOAD_MAX_ATTEMPTS="${DOWNLOAD_MAX_ATTEMPTS:-3}"

_explain_download_failure() {
	local url="$1"
	local dest="$2"
	local parent
	parent="$(dirname "$dest")"

	log_error "Failed to download after ${DOWNLOAD_MAX_ATTEMPTS} attempt(s): ${url}"
	log_error "curl exit 23 means 'Failure writing output to destination'"
	log_error "Common causes: disk full, unwritable directory, or transient I/O"
	log_error "Destination: ${dest}"
	if [[ -d "$parent" ]]; then
		if [[ -w "$parent" ]]; then
			log_error "Parent directory writable: yes"
		else
			log_error "Parent directory writable: no"
		fi
		log_error "Disk space for ${parent}:"
		df -h "$parent" 2>/dev/null || true
	fi
}

_download_or_fail() {
	local url="$1"
	local dest="$2"
	local label="$3"

	log_info "Downloading ${label} from ${url}..."
	if download_with_retries "$url" "$dest" "$DOWNLOAD_MAX_ATTEMPTS"; then
		return 0
	fi
	_explain_download_failure "$url" "$dest"
	return 1
}

OS=$(uname -s)
if [[ "$OS" != "Linux" ]]; then
	log_error "osv-scanner install supports Linux runners only (detected: $OS)"
	exit 1
fi

ARCH=$(uname -m)
case "$ARCH" in
x86_64) PLATFORM="linux_amd64" ;;
aarch64 | arm64) PLATFORM="linux_arm64" ;;
*)
	log_error "Unsupported architecture: $ARCH"
	exit 1
	;;
esac

BASE_URL="https://github.com/google/osv-scanner/releases/download/v${OSV_VERSION}"
BINARY_URL="${BASE_URL}/osv-scanner_${PLATFORM}"
CHECKSUMS_URL="${BASE_URL}/osv-scanner_SHA256SUMS"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
if [[ ! -w "$INSTALL_DIR" ]]; then
	INSTALL_DIR="${HOME}/.local/bin"
	mkdir -p "$INSTALL_DIR"
fi

WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/py-lintro-osv.XXXXXXXXXX")
trap 'rm -rf "$WORKDIR"' EXIT

log_info "Installing osv-scanner v${OSV_VERSION} (${PLATFORM}) to ${INSTALL_DIR}..."
log_info "Temporary workdir: ${WORKDIR}"

_download_or_fail "$BINARY_URL" "${WORKDIR}/osv-scanner" "osv-scanner binary"
_download_or_fail "$CHECKSUMS_URL" "${WORKDIR}/SHA256SUMS" "SHA256SUMS"

CHECKSUMS_SIG_URL="${BASE_URL}/osv-scanner_SHA256SUMS.sig"
CHECKSUMS_CERT_URL="${BASE_URL}/osv-scanner_SHA256SUMS.pem"
if download_with_retries "$CHECKSUMS_SIG_URL" "${WORKDIR}/SHA256SUMS.sig" 1 2>/dev/null &&
	download_with_retries "$CHECKSUMS_CERT_URL" "${WORKDIR}/SHA256SUMS.pem" 1 2>/dev/null; then
	if command -v cosign >/dev/null 2>&1; then
		log_info "Verifying SHA256SUMS sigstore signature..."
		cosign verify-blob \
			--signature "${WORKDIR}/SHA256SUMS.sig" \
			--certificate "${WORKDIR}/SHA256SUMS.pem" \
			--certificate-identity-regexp='https://github\.com/google/osv-scanner/.*' \
			--certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
			"${WORKDIR}/SHA256SUMS"
		log_success "SHA256SUMS signature verified"
	else
		log_warn "cosign not found; skipping SHA256SUMS signature verification"
	fi
else
	log_info "Release does not publish SHA256SUMS sigstore assets; verifying binary checksum only"
fi

EXPECTED=$(
	awk -v fn="osv-scanner_${PLATFORM}" '$2 == fn { print $1; exit }' "${WORKDIR}/SHA256SUMS"
)
if [[ -z "$EXPECTED" ]]; then
	log_error "No checksum entry for osv-scanner_${PLATFORM} in SHA256SUMS"
	exit 1
fi

printf '%s  osv-scanner\n' "$EXPECTED" | (
	cd "${WORKDIR}" && sha256sum -c -
)
log_success "SHA256 verified: ${EXPECTED}"

chmod +x "${WORKDIR}/osv-scanner"
mv "${WORKDIR}/osv-scanner" "${INSTALL_DIR}/osv-scanner"

"${INSTALL_DIR}/osv-scanner" --version
log_success "osv-scanner v${OSV_VERSION} installed to ${INSTALL_DIR}"

if [[ ":$PATH:" != *":${INSTALL_DIR}:"* ]]; then
	export PATH="${INSTALL_DIR}:${PATH}"
	if [[ -n "${GITHUB_PATH:-}" ]]; then
		echo "$INSTALL_DIR" >>"$GITHUB_PATH"
	fi
fi
