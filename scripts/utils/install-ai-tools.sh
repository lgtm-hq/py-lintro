#!/usr/bin/env bash
set -euo pipefail

# install-ai-tools.sh - installer for the AI agent CLIs lintro shells out to
#
# lintro's `--transport cli` providers drive three external agent binaries:
# `claude`, `codex` and Cursor's `agent`. Their names, and the flag surface
# lintro requires of each, are declared in lintro/ai/providers/cli_contracts.py
# -- that module is the source of truth; this script only has to make those
# binaries exist on PATH.
#
# Everything lands under a single prefix ($AI_TOOLS_PREFIX, default
# /opt/ai-tools) so the consuming image can pick it up with one COPY --from:
#
#   $PREFIX/node/       pinned Node.js runtime + npm, and the npm-global
#                       `claude` / `codex` install trees
#   $PREFIX/cursor/     Cursor agent release tree (standalone native binary)
#   $PREFIX/bin/        launcher shims -- the only directory that needs to be
#                       on PATH
#
# The bundled Node.js is deliberate rather than redundant: the lintro-tools
# base image symlinks `node` to bun, and the agent CLIs are not bun-compatible.
# The shims prepend $PREFIX/node/bin to PATH for their own process only, so the
# lint toolchain keeps resolving `node` to bun exactly as before.
#
# Usage:
#   NODE_VERSION=24.18.0 \
#   CLAUDE_CODE_VERSION=2.1.220 \
#   CODEX_VERSION=0.145.0 \
#   CURSOR_AGENT_VERSION=2026.07.23-e383d2b \
#   CURSOR_AGENT_SHA256_X64=<x64 hash> CURSOR_AGENT_SHA256_ARM64=<arm64 hash> \
#     ./scripts/utils/install-ai-tools.sh
#
# Environment:
#   NODE_VERSION           Node.js version, no leading "v"       (required)
#   CLAUDE_CODE_VERSION    npm @anthropic-ai/claude-code version (required)
#   CODEX_VERSION          npm @openai/codex version             (required)
#   CURSOR_AGENT_VERSION   Cursor agent release id               (required)
#   CURSOR_AGENT_SHA256_X64    sha256 of the linux/x64 Cursor tarball  (required)
#   CURSOR_AGENT_SHA256_ARM64  sha256 of the linux/arm64 tarball       (required)
#   AI_TOOLS_PREFIX        Install prefix (default /opt/ai-tools)
#
# Cursor publishes no checksum sidecar next to the agent tarball, so unlike the
# Node.js download there is nothing to fetch and verify against. Both
# architectures' hashes are therefore pinned by hand alongside the version and
# required here, rather than left opt-in: the agent runs against user
# codebases and API keys, so a silently substituted binary is not an
# acceptable failure mode.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: install-ai-tools.sh [--help]

AI Agent CLI Installation Script
Installs the agent CLIs lintro's `--transport cli` providers shell out to
(claude, codex, Cursor's agent) under a single prefix, together with the
Node.js runtime they need.

Options:
  --help, -h     Show this help message

Environment:
  NODE_VERSION           Node.js version, no leading "v"       (required)
  CLAUDE_CODE_VERSION    npm @anthropic-ai/claude-code version (required)
  CODEX_VERSION          npm @openai/codex version             (required)
  CURSOR_AGENT_VERSION   Cursor agent release id               (required)
  CURSOR_AGENT_SHA256_X64    sha256 of the linux/x64 tarball   (required)
  CURSOR_AGENT_SHA256_ARM64  sha256 of the linux/arm64 tarball (required)
  AI_TOOLS_PREFIX        Install prefix (default /opt/ai-tools)
EOF
	exit 0
fi

PREFIX="${AI_TOOLS_PREFIX:-/opt/ai-tools}"

log_step() {
	echo "==> $1"
}

require_env() {
	local name="$1"
	if [ -z "${!name:-}" ]; then
		echo "ERROR: $name is required" >&2
		exit 1
	fi
}

require_env NODE_VERSION
require_env CLAUDE_CODE_VERSION
require_env CODEX_VERSION
require_env CURSOR_AGENT_VERSION
require_env CURSOR_AGENT_SHA256_X64
require_env CURSOR_AGENT_SHA256_ARM64

# Both vendors label the same two architectures identically, so one mapping
# serves the Node.js tarball and the Cursor download alike.
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

install_node() {
	log_step "Installing Node.js ${NODE_VERSION} (${ARCH})"
	local tarball url base tmp
	# .tar.gz rather than the smaller .tar.xz: xz-utils is not in the base
	# image and pulling it in just to unpack one tarball is not worth a layer.
	tarball="node-v${NODE_VERSION}-linux-${ARCH}.tar.gz"
	base="https://nodejs.org/dist/v${NODE_VERSION}"
	url="${base}/${tarball}"
	tmp="$(mktemp -d)"

	curl -fsSL "$url" -o "${tmp}/${tarball}"
	curl -fsSL "${base}/SHASUMS256.txt" -o "${tmp}/SHASUMS256.txt"
	(cd "$tmp" && grep " ${tarball}\$" SHASUMS256.txt | sha256sum -c -)

	mkdir -p "${PREFIX}/node"
	tar -xzf "${tmp}/${tarball}" -C "${PREFIX}/node" --strip-components=1
	rm -rf "$tmp"
}

install_npm_clis() {
	log_step "Installing claude ${CLAUDE_CODE_VERSION} and codex ${CODEX_VERSION}"
	# Both packages resolve a platform-specific native binary through
	# optionalDependencies plus a postinstall step, so they must be installed
	# with the real npm on the target architecture -- never with --ignore-scripts.
	PATH="${PREFIX}/node/bin:${PATH}" "${PREFIX}/node/bin/npm" install \
		--global \
		--prefix "${PREFIX}/node" \
		--no-fund \
		--no-audit \
		"@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
		"@openai/codex@${CODEX_VERSION}"
}

install_cursor_agent() {
	log_step "Installing Cursor agent ${CURSOR_AGENT_VERSION} (${ARCH})"
	local url dir tmp expected
	# Same artifact layout the official https://cursor.com/install script
	# downloads, pinned to an explicit release instead of whatever that script
	# currently embeds.
	url="https://downloads.cursor.com/lab/${CURSOR_AGENT_VERSION}/linux/${ARCH}/agent-cli-package.tar.gz"
	dir="${PREFIX}/cursor/${CURSOR_AGENT_VERSION}"
	tmp="$(mktemp -d)"

	case "$ARCH" in
	x64) expected="$CURSOR_AGENT_SHA256_X64" ;;
	*) expected="$CURSOR_AGENT_SHA256_ARM64" ;;
	esac

	curl -fsSL "$url" -o "${tmp}/agent-cli-package.tar.gz"
	echo "${expected}  ${tmp}/agent-cli-package.tar.gz" | sha256sum -c -

	mkdir -p "$dir"
	tar -xzf "${tmp}/agent-cli-package.tar.gz" -C "$dir" --strip-components=1
	rm -rf "$tmp"
}

# Write a launcher that runs *target* with the bundled Node.js ahead of the
# image's bun-as-node symlink on PATH.
write_shim() {
	local name="$1" target="$2"
	cat >"${PREFIX}/bin/${name}" <<EOF
#!/bin/sh
# Generated by scripts/utils/install-ai-tools.sh -- do not edit.
# The lintro-tools base image aliases \`node\` to bun; the agent CLIs need the
# real runtime, so put it first for this process only.
PATH="${PREFIX}/node/bin:\$PATH"
export PATH
exec "${target}" "\$@"
EOF
	chmod +x "${PREFIX}/bin/${name}"
}

install_shims() {
	log_step "Writing launcher shims"
	mkdir -p "${PREFIX}/bin"
	write_shim "claude" "${PREFIX}/node/bin/claude"
	write_shim "codex" "${PREFIX}/node/bin/codex"
	# `agent` is the binary name lintro's Cursor provider looks up;
	# `cursor-agent` is the vendor's legacy alias, kept for parity with the
	# official installer.
	write_shim "agent" "${PREFIX}/cursor/${CURSOR_AGENT_VERSION}/cursor-agent"
	write_shim "cursor-agent" "${PREFIX}/cursor/${CURSOR_AGENT_VERSION}/cursor-agent"
}

relax_permissions() {
	log_step "Relaxing permissions for non-root use"
	# The consuming image drops privileges to the UID owning the mounted
	# workspace, so every baked binary has to stay readable and executable for
	# arbitrary users.
	chmod -R a+rX "${PREFIX}"
}

verify() {
	log_step "Verifying AI agent CLIs"
	PATH="${PREFIX}/bin:${PATH}" claude --version
	PATH="${PREFIX}/bin:${PATH}" codex --version
	PATH="${PREFIX}/bin:${PATH}" agent --version
	log_step "All AI agent CLIs verified"
}

install_node
install_npm_clis
install_cursor_agent
install_shims
relax_permissions
verify
