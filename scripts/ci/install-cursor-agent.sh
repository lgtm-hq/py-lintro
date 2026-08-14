#!/usr/bin/env bash
set -euo pipefail

# install-cursor-agent.sh - install the pinned Cursor `agent` CLI on a runner
#
# The AI review dogfood can overlay `LINTRO_AI_PROVIDER=cursor` via repo Actions
# variables (#1971). lintro's Cursor provider shells out to the `agent` binary
# and authenticates with CURSOR_API_KEY; the binary therefore has to exist on
# the runner. The version and tarball hashes are not chosen here: the caller
# resolves them from the ai-tools Dockerfile's ARG pins
# (scripts/ci/ai_tools_arg_pin.py), so the CLI the dogfood drives is the same
# one the released `ai` image ships.
#
# Cursor publishes no checksum sidecar, so the hash is pinned by hand next to
# the version and required here. An unpinned `curl https://cursor.com/install`
# would install an unreviewed binary into a job that holds a credential.
#
# Usage:
#   CURSOR_AGENT_VERSION=2026.07.23-e383d2b \
#   CURSOR_AGENT_SHA256_X64=<sha256> \
#     scripts/ci/install-cursor-agent.sh
#
# Environment:
#   CURSOR_AGENT_VERSION      Calendar build id from the Dockerfile ARG
#   CURSOR_AGENT_SHA256_X64   sha256 of the linux/x64 tarball (required on x64)
#   CURSOR_AGENT_SHA256_ARM64 sha256 of the linux/arm64 tarball (required on arm64)
#   AI_TOOLS_PREFIX           Install prefix (default: $RUNNER_TEMP/ai-tools
#                             or /tmp/ai-tools)

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: CURSOR_AGENT_VERSION=<id> CURSOR_AGENT_SHA256_<ARCH>=<sha256> \
  scripts/ci/install-cursor-agent.sh

Install the pinned Cursor agent CLI and verify it answers --version.

Environment:
  CURSOR_AGENT_VERSION      Calendar build id (required, from the Dockerfile ARG)
  CURSOR_AGENT_SHA256_X64   sha256 of the linux/x64 tarball
  CURSOR_AGENT_SHA256_ARM64 sha256 of the linux/arm64 tarball
  AI_TOOLS_PREFIX           Install prefix (default: $RUNNER_TEMP/ai-tools)
EOF
	exit 0
fi

if [[ -z "${CURSOR_AGENT_VERSION:-}" ]]; then
	echo "ERROR: CURSOR_AGENT_VERSION is required" >&2
	exit 1
fi

# Calendar build ids look like 2026.07.23-e383d2b. Refuse anything that could
# change the download URL into a different host or path.
if [[ ! "${CURSOR_AGENT_VERSION}" =~ ^[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[A-Za-z0-9]+$ ]]; then
	echo "ERROR: CURSOR_AGENT_VERSION must be a calendar build id (YYYY.MM.DD-<rev>)" >&2
	exit 1
fi

detect_arch() {
	local machine
	machine="$(uname -m)"
	case "$machine" in
	x86_64 | amd64) echo "x64" ;;
	aarch64 | arm64) echo "arm64" ;;
	*)
		echo "ERROR: unsupported architecture: $machine" >&2
		exit 1
		;;
	esac
}

ARCH="$(detect_arch)"
case "$ARCH" in
x64)
	if [[ -z "${CURSOR_AGENT_SHA256_X64:-}" ]]; then
		echo "ERROR: CURSOR_AGENT_SHA256_X64 is required on x64" >&2
		exit 1
	fi
	expected="$CURSOR_AGENT_SHA256_X64"
	;;
*)
	if [[ -z "${CURSOR_AGENT_SHA256_ARM64:-}" ]]; then
		echo "ERROR: CURSOR_AGENT_SHA256_ARM64 is required on arm64" >&2
		exit 1
	fi
	expected="$CURSOR_AGENT_SHA256_ARM64"
	;;
esac

if [[ ! "${expected}" =~ ^[a-fA-F0-9]{64}$ ]]; then
	echo "ERROR: Cursor agent sha256 must be a 64-character hex digest" >&2
	exit 1
fi

PREFIX="${AI_TOOLS_PREFIX:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ai-tools}"
url="https://downloads.cursor.com/lab/${CURSOR_AGENT_VERSION}/linux/${ARCH}/agent-cli-package.tar.gz"
dir="${PREFIX}/cursor/${CURSOR_AGENT_VERSION}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Installing Cursor agent ${CURSOR_AGENT_VERSION} (${ARCH})..."
curl -fsSL "$url" -o "${tmp}/agent-cli-package.tar.gz"
echo "${expected}  ${tmp}/agent-cli-package.tar.gz" | sha256sum -c -

mkdir -p "${PREFIX}/bin" "$dir"
tar -xzf "${tmp}/agent-cli-package.tar.gz" -C "$dir" --strip-components=1

if [[ ! -x "${dir}/cursor-agent" ]]; then
	echo "ERROR: unpacked tarball has no executable cursor-agent binary" >&2
	exit 1
fi

# Native binary — no Node shim. `agent` is the name lintro looks up;
# `cursor-agent` is the vendor's legacy alias.
ln -sfn "${dir}/cursor-agent" "${PREFIX}/bin/agent"
ln -sfn "${dir}/cursor-agent" "${PREFIX}/bin/cursor-agent"

if [[ -n "${GITHUB_PATH:-}" ]]; then
	echo "${PREFIX}/bin" >>"${GITHUB_PATH}"
fi
export PATH="${PREFIX}/bin:${PATH}"

echo "Verifying the installed CLI answers --version..."
agent --version
