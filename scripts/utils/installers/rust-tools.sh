#!/usr/bin/env bash
# Rust toolchain and cargo tool installers.
# Sourced by install-tools.sh or runnable directly.
set -euo pipefail

_RUST_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_helpers.sh disable=SC1091
source "$_RUST_TOOLS_DIR/_helpers.sh"

# Shared helper: ensure Rust toolchain is installed with the required component.
# Called by both the rustfmt and clippy blocks to avoid duplicating toolchain setup.
# Usage: ensure_rust_toolchain <component>  (e.g. "rustfmt" or "clippy")
ensure_rust_toolchain() {
	local component="$1"

	if [ -z "${RUST_TOOLCHAIN_VERSION:-}" ]; then
		# Clippy versions match Rust release versions (clippy 1.94.0 = Rust 1.94.0).
		# Use the highest version among rustc and clippy so that Renovate PRs
		# that bump clippy independently install the correct toolchain.
		local _rustc_ver _clippy_ver
		_rustc_ver=$(get_tool_version "rustc" 2>/dev/null || echo "")
		_clippy_ver=$(get_tool_version "clippy" 2>/dev/null || echo "")

		if [ -n "$_rustc_ver" ] && [ -n "$_clippy_ver" ]; then
			RUST_TOOLCHAIN_VERSION=$(printf '%s\n%s\n' "$_rustc_ver" "$_clippy_ver" | sort -V | tail -1)
		elif [ -n "$_rustc_ver" ]; then
			RUST_TOOLCHAIN_VERSION="$_rustc_ver"
		elif [ -n "$_clippy_ver" ]; then
			RUST_TOOLCHAIN_VERSION="$_clippy_ver"
		else
			RUST_TOOLCHAIN_VERSION="stable"
			echo -e "${YELLOW}⚠ Rust toolchain version not found in manifest; using stable${NC}"
		fi
		log_verbose "Pinned Rust toolchain version: ${RUST_TOOLCHAIN_VERSION}"
	fi

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install Rust toolchain (${RUST_TOOLCHAIN_VERSION}) with ${component}"
		return 0
	fi

	# Check if toolchain + component already available.
	# clippy is a cargo subcommand, not a standalone binary.
	_component_available() {
		if [ "$1" = "clippy" ]; then
			cargo clippy --version &>/dev/null
		else
			command -v "$1" &>/dev/null
		fi
	}
	if command -v rustc &>/dev/null && _component_available "$component"; then
		if [ "$RUST_TOOLCHAIN_VERSION" = "stable" ]; then
			echo -e "${GREEN}✓ ${component} already installed${NC}"
			return 0
		fi
		local installed_version
		installed_version=$(rustc --version 2>/dev/null | awk '{print $2}')
		if [ -n "$installed_version" ] && [ "$installed_version" = "$RUST_TOOLCHAIN_VERSION" ]; then
			echo -e "${GREEN}✓ ${component} already installed${NC}"
			return 0
		fi
		echo -e "${YELLOW}⚠ rustc version ${installed_version:-unknown} != ${RUST_TOOLCHAIN_VERSION}, reinstalling...${NC}"
	fi

	# Install rustup if not present
	if ! command -v rustup &>/dev/null; then
		echo -e "${YELLOW}Installing rustup...${NC}"
		curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
			--default-toolchain "$RUST_TOOLCHAIN_VERSION" --component "$component"
		# Source cargo environment (respect CARGO_HOME if set)
		local cargo_env
		cargo_env="${CARGO_HOME:-$HOME/.cargo}/env"
		if [ -f "$cargo_env" ]; then
			# SC1090: cargo env is created by rustup installer at runtime
			# shellcheck disable=SC1090
			source "$cargo_env"
		fi
	else
		echo -e "${YELLOW}rustup already installed, ensuring ${component}...${NC}"
		if [ "$RUST_TOOLCHAIN_VERSION" = "stable" ]; then
			rustup update stable
			rustup component add "$component"
		else
			rustup toolchain install "$RUST_TOOLCHAIN_VERSION"
			rustup default "$RUST_TOOLCHAIN_VERSION"
			rustup component add "$component" --toolchain "$RUST_TOOLCHAIN_VERSION"
		fi
	fi

	# Verify
	if ! _component_available "$component"; then
		echo -e "${RED}✗ Failed to install ${component}${NC}"
		exit 1
	fi
	if [ "$RUST_TOOLCHAIN_VERSION" != "stable" ]; then
		local installed_version
		installed_version=$(rustc --version 2>/dev/null | awk '{print $2}')
		if [ -z "$installed_version" ] || [ "$installed_version" != "$RUST_TOOLCHAIN_VERSION" ]; then
			echo -e "${RED}✗ rustc version mismatch (expected ${RUST_TOOLCHAIN_VERSION}, got ${installed_version:-unknown})${NC}"
			exit 1
		fi
	fi
	echo -e "${GREEN}✓ ${component} installed successfully${NC}"
}

install_rustfmt() {
	if ! should_install "rustfmt"; then
		log_verbose "Skipping rustfmt (not in --tools filter)"
		return 0
	fi
	echo -e "${BLUE}Installing rustfmt...${NC}"
	ensure_rust_toolchain "rustfmt"
}

install_clippy() {
	if ! should_install "clippy"; then
		log_verbose "Skipping clippy (not in --tools filter)"
		return 0
	fi
	echo -e "${BLUE}Installing clippy...${NC}"
	# clippy is invoked via cargo, verify with cargo clippy
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install clippy"
	elif command -v cargo &>/dev/null && cargo clippy --version &>/dev/null; then
		echo -e "${GREEN}✓ clippy already installed${NC}"
	else
		ensure_rust_toolchain "clippy"
	fi
}

install_cargo_audit() {
	if ! should_install "cargo-audit"; then
		log_verbose "Skipping cargo-audit (not in --tools filter)"
		return 0
	fi
	# Install cargo-audit (Rust dependency vulnerability scanner)
	# Prefer pre-built binary from cargo-quickinstall to avoid 20+ minute compile times
	echo -e "${BLUE}Installing cargo-audit...${NC}"
	local CARGO_AUDIT_VERSION
	CARGO_AUDIT_VERSION=$(get_tool_version "cargo_audit") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install cargo-audit==${CARGO_AUDIT_VERSION}"
	elif command -v cargo-audit &>/dev/null; then
		echo -e "${GREEN}✓ cargo-audit already installed${NC}"
	else
		local cargo_audit_installed=false
		local tmpdir os arch target tgz_url
		# Try pre-built binary from cargo-quickinstall first (much faster than cargo install)
		tmpdir=$(mktemp -d)
		os=$(uname -s | tr '[:upper:]' '[:lower:]')
		arch=$(uname -m)
		case "$arch" in
		x86_64 | amd64) target="x86_64-unknown-linux-gnu" ;;
		aarch64 | arm64) target="aarch64-unknown-linux-gnu" ;;
		*) target="" ;;
		esac
		# cargo-quickinstall only provides linux binaries
		if [[ "$os" == "linux" ]] && [[ -n "$target" ]]; then
			tgz_url="https://github.com/cargo-bins/cargo-quickinstall/releases/download/cargo-audit-${CARGO_AUDIT_VERSION}/cargo-audit-${CARGO_AUDIT_VERSION}-${target}.tar.gz"
			echo -e "${YELLOW}Trying pre-built binary from cargo-quickinstall...${NC}"
			if download_with_retries "$tgz_url" "$tmpdir/cargo-audit.tar.gz" 3; then
				tar -xzf "$tmpdir/cargo-audit.tar.gz" -C "$tmpdir"
				if [ -f "$tmpdir/cargo-audit" ]; then
					cp "$tmpdir/cargo-audit" "$BIN_DIR/cargo-audit"
					chmod +x "$BIN_DIR/cargo-audit"
					echo -e "${GREEN}✓ cargo-audit installed from pre-built binary${NC}"
					cargo_audit_installed=true
				fi
			fi
		fi
		rm -rf "$tmpdir"

		# Fallback to cargo install if pre-built binary not available
		if [ "$cargo_audit_installed" = false ] && command -v cargo &>/dev/null; then
			echo -e "${YELLOW}Pre-built binary not available, falling back to cargo install...${NC}"
			ensure_cargo_audit_deps
			if cargo install cargo-audit --locked --version "$CARGO_AUDIT_VERSION"; then
				echo -e "${GREEN}✓ cargo-audit installed via cargo${NC}"
				cargo_audit_installed=true
			fi
		fi

		if [ "$cargo_audit_installed" = false ]; then
			echo -e "${YELLOW}⚠ Failed to install cargo-audit (optional tool)${NC}"
		fi
	fi
}

install_cargo_deny() {
	if ! should_install "cargo-deny"; then
		log_verbose "Skipping cargo-deny (not in --tools filter)"
		return 0
	fi
	# Install cargo-deny (Rust dependency license/advisory checker)
	# Prefer pre-built binary from cargo-quickinstall to avoid long compile times
	echo -e "${BLUE}Installing cargo-deny...${NC}"
	local CARGO_DENY_VERSION
	CARGO_DENY_VERSION=$(get_tool_version "cargo_deny") || exit 1
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install cargo-deny==${CARGO_DENY_VERSION}"
	elif command -v cargo-deny &>/dev/null; then
		echo -e "${GREEN}✓ cargo-deny already installed${NC}"
	else
		local cargo_deny_installed=false
		local tmpdir os arch target tgz_url
		# Try pre-built binary from cargo-quickinstall first (much faster than cargo install)
		tmpdir=$(mktemp -d)
		os=$(uname -s | tr '[:upper:]' '[:lower:]')
		arch=$(uname -m)
		case "$arch" in
		x86_64 | amd64) target="x86_64-unknown-linux-gnu" ;;
		aarch64 | arm64) target="aarch64-unknown-linux-gnu" ;;
		*) target="" ;;
		esac
		# cargo-quickinstall only provides linux binaries
		if [[ "$os" == "linux" ]] && [[ -n "$target" ]]; then
			tgz_url="https://github.com/cargo-bins/cargo-quickinstall/releases/download/cargo-deny-${CARGO_DENY_VERSION}/cargo-deny-${CARGO_DENY_VERSION}-${target}.tar.gz"
			echo -e "${YELLOW}Trying pre-built binary from cargo-quickinstall...${NC}"
			if download_with_retries "$tgz_url" "$tmpdir/cargo-deny.tar.gz" 3; then
				tar -xzf "$tmpdir/cargo-deny.tar.gz" -C "$tmpdir"
				if [ -f "$tmpdir/cargo-deny" ]; then
					cp "$tmpdir/cargo-deny" "$BIN_DIR/cargo-deny"
					chmod +x "$BIN_DIR/cargo-deny"
					echo -e "${GREEN}✓ cargo-deny installed from pre-built binary${NC}"
					cargo_deny_installed=true
				fi
			fi
		fi
		rm -rf "$tmpdir"

		# Fallback to cargo install if pre-built binary not available
		if [ "$cargo_deny_installed" = false ] && command -v cargo &>/dev/null; then
			echo -e "${YELLOW}Pre-built binary not available, falling back to cargo install...${NC}"
			ensure_cargo_audit_deps
			if cargo install cargo-deny --locked --version "$CARGO_DENY_VERSION"; then
				echo -e "${GREEN}✓ cargo-deny installed via cargo${NC}"
				cargo_deny_installed=true
			fi
		fi

		if [ "$cargo_deny_installed" = false ]; then
			echo -e "${YELLOW}⚠ Failed to install cargo-deny (optional tool)${NC}"
		fi
	fi
}

install_rust_tools() {
	install_rustfmt
	install_clippy
	install_cargo_audit
	install_cargo_deny
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
		cat <<'EOF'
Usage: rust-tools.sh [--help]
Install Rust toolchain tools (rustfmt, clippy, cargo-audit, cargo-deny).

Respects env: DRY_RUN, TOOL_FILTER, BIN_DIR, INSTALL_MODE, VERBOSE.
Usually invoked via scripts/utils/install-tools.sh.
EOF
		exit 0
	fi
	ensure_bin_dir
	install_rust_tools "$@"
fi
