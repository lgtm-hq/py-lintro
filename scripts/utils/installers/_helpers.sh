#!/usr/bin/env bash
# Shared helpers for lintro tool installers.
# Sourced by install-tools.sh and the group installer scripts.
#
# Expects (or sets defaults for): DRY_RUN, VERBOSE, TOOL_FILTER, BIN_DIR,
# INSTALL_MODE, PROJECT_ROOT.

# Prevent double-loading when multiple group installers source this file.
if [[ -n "${_LINTRO_INSTALL_HELPERS_LOADED:-}" ]]; then
	return 0
fi
_LINTRO_INSTALL_HELPERS_LOADED=1

_HELPERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# installers/ -> utils/ -> scripts/ -> repo root
: "${PROJECT_ROOT:=$(cd "$_HELPERS_DIR/../../.." && pwd)}"

# SC1091: path is dynamically constructed, file exists at runtime
# shellcheck source=../utils.sh disable=SC1091
source "$_HELPERS_DIR/../utils.sh"

# Color constants are provided by utils.sh (RED, GREEN, BLUE, YELLOW, NC).
# log_verbose is also provided by utils.sh.

# Defaults for globals when a group installer is sourced/run directly.
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-0}"
TOOL_FILTER="${TOOL_FILTER:-}"
INSTALL_MODE="${INSTALL_MODE:-local}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

# Get tool version from lintro/_tool_versions.py
# This module is the single source of truth for all tool versions
# Uses runpy.run_path() to execute the file directly without package installation
# Note: TOOL_VERSIONS uses ToolName enum keys; get_tool_version handles aliases
get_tool_version() {
	local tool_name="$1"
	local version
	version=$(python3 -c "
import runpy
import sys

sys.path.insert(0, '$PROJECT_ROOT')
mod = runpy.run_path('$PROJECT_ROOT/lintro/_tool_versions.py')
# Use get_tool_version which handles ToolName keys and package aliases
version = mod['get_tool_version']('$tool_name')
if version:
    print(version)
else:
    sys.exit(1)
" 2>/dev/null)
	if [ -z "$version" ]; then
		echo "ERROR: Version for '$tool_name' not found in lintro/_tool_versions.py" >&2
		return 1
	fi
	echo "$version"
}

# Tool filter: when --tools is set, only install the listed tools.
# Accepts comma-separated names matching the install block identifiers
# (e.g., osv-scanner, hadolint, ruff). Normalizes underscores to hyphens.
should_install() {
	local tool_name="$1"
	# No filter = install everything
	[[ -z "$TOOL_FILTER" ]] && return 0
	# Normalize: replace underscores with hyphens for matching
	local normalized_filter="${TOOL_FILTER//_/-}"
	local normalized_name="${tool_name//_/-}"
	# Check if tool is in the comma-separated list
	IFS=',' read -ra filter_list <<<"$normalized_filter"
	for item in "${filter_list[@]}"; do
		# Trim whitespace
		item="${item## }"
		item="${item%% }"
		[[ "$item" == "$normalized_name" ]] && return 0
	done
	return 1
}

# Script-specific logging (prefixed)
install_log() { echo "[install-tools] $*"; }

# Helper function to ensure bun is installed
# Returns 0 on success, non-zero on failure (does not call exit)
ensure_bun_installed() {
	if command -v bun &>/dev/null; then
		return 0
	fi

	echo -e "${YELLOW}bun not found, trying to install...${NC}"

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install bun via curl"
		return 0
	fi

	# Install bun via official installer (works on Linux and macOS)
	# Justification: Official bun installer from trusted source (bun.sh)
	# nosemgrep: curl-pipe-bash
	if curl -fsSL https://bun.sh/install | bash; then
		# Source bun environment (respect existing BUN_INSTALL if set)
		if [ -z "${BUN_INSTALL:-}" ] && [ -f "$HOME/.bun/bin/bun" ]; then
			export BUN_INSTALL="$HOME/.bun"
		fi
		export PATH="${BUN_INSTALL:-$HOME/.bun}/bin:$PATH"
		return 0
	fi

	# Try Homebrew on macOS as fallback
	if command -v brew &>/dev/null; then
		echo -e "${YELLOW}Trying Homebrew for bun...${NC}"
		if brew install oven-sh/bun/bun; then
			return 0
		fi
	fi

	echo -e "${RED}✗ Cannot install bun automatically. Please install bun manually: https://bun.sh${NC}"
	return 1
}

# Simple downloader with retries/backoff
download_with_retries() {
	local url="$1"
	shift
	local out="$1"
	shift
	local attempts=${1:-3}
	local delay=0.5
	local i
	for ((i = 1; i <= attempts; i++)); do
		if [ "$DRY_RUN" -eq 1 ]; then
			log_info "[DRY-RUN] Would download $url to $out"
			return 0
		fi
		if curl -fsSL "$url" -o "$out"; then
			return 0
		fi
		sleep "$delay"
		delay=$(awk -v d="$delay" 'BEGIN{ printf "%.2f", d*2 }')
	done
	return 1
}

# Function to detect platform and architecture
detect_platform() {
	local os
	local arch
	os=$(uname -s)
	arch=$(uname -m)

	# Normalize OS names for hadolint
	case "$os" in
	Darwin) os="Darwin" ;;
	Linux) os="Linux" ;;
	MINGW* | MSYS* | CYGWIN*) os="Windows" ;;
	*) ;; # keep original value
	esac

	# Normalize architecture names for hadolint
	case "$arch" in
	x86_64) arch="x86_64" ;;
	amd64) arch="x86_64" ;;
	aarch64) arch="arm64" ;;
	arm64) arch="arm64" ;;
	*) ;; # keep original value
	esac

	echo "${os}-${arch}"
}

# Function to install Python package with fallbacks (uv pip preferred)
install_python_package() {
	local package="$1"
	local version="${2:-}"
	local full_package="$package"

	if ! should_install "$package"; then
		log_verbose "Skipping $package (not in --tools filter)"
		return 0
	fi

	if [ -n "$version" ]; then
		full_package="$package==$version"
	fi

	# Prefer uv pip when available
	if command -v uv &>/dev/null; then
		if uv pip install "$full_package"; then
			# Copy the executable to target directory if it exists in uv environment
			local uv_path
			uv_path=$(uv run which "$package" 2>/dev/null || echo "")
			if [ -n "$uv_path" ] && [ -f "$uv_path" ]; then
				cp "$uv_path" "$BIN_DIR/$package"
				chmod +x "$BIN_DIR/$package"
				echo -e "${YELLOW}Copied $package from uv environment to $BIN_DIR${NC}"
			fi
			return 0
		fi
	fi

	# Fallback to pip
	if command -v pip &>/dev/null; then
		if pip install "$full_package"; then
			return 0
		fi
	fi

	# Try system package managers as last resort
	if command -v brew &>/dev/null; then
		if brew install "$package"; then
			return 0
		fi
	fi

	return 1
}

# Function to install a tool via curl with platform detection
install_tool_curl() {
	local tool_name="$1"
	local base_url="$2"
	local target_path="$BIN_DIR/$tool_name"

	if ! should_install "$tool_name"; then
		log_verbose "Skipping $tool_name (not in --tools filter)"
		return 0
	fi

	echo -e "${BLUE}Installing $tool_name...${NC}"

	# Get platform info
	local platform
	platform=$(detect_platform)
	local download_url="${base_url}-${platform}"

	echo -e "${YELLOW}Detected platform: $platform${NC}"
	echo -e "${YELLOW}Download URL: $download_url${NC}"

	# Dry-run: download_with_retries logs intent but does not create files.
	# Avoid chmod/checksum steps that would fail on a missing target.
	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install $tool_name from $download_url"
		if [[ "$tool_name" == "hadolint" ]]; then
			log_info "[DRY-RUN] Would verify checksum for $tool_name"
		fi
		echo -e "${GREEN}✓ $tool_name installed successfully${NC}"
		return 0
	fi

	if download_with_retries "$download_url" "$target_path" 3; then
		chmod +x "$target_path"
		# Attempt checksum verification when available
		if [[ "$tool_name" == "hadolint" ]]; then
			local checksum_url="${download_url}.sha256"
			if download_with_retries "$checksum_url" "$target_path.sha256" 3; then
				echo -e "${BLUE}Verifying checksum for $tool_name...${NC}"
				# Portable verification regardless of filename in .sha256
				local expected
				expected=$(awk '{print $1}' "$target_path.sha256" | head -n1)
				local actual
				if command -v sha256sum >/dev/null 2>&1; then
					actual=$(sha256sum "$target_path" | awk '{print $1}')
				elif command -v shasum >/dev/null 2>&1; then
					actual=$(shasum -a 256 "$target_path" | awk '{print $1}')
				else
					echo -e "${YELLOW}⚠ No checksum tool available; skipping verification${NC}"
					actual=""
				fi
				if [[ -n "$actual" ]]; then
					if [[ "$expected" != "$actual" ]]; then
						echo -e "${RED}✗ Checksum mismatch for $tool_name${NC}"
						exit 1
					fi
					echo -e "${GREEN}✓ Checksum verified${NC}"
				fi
				rm -f "$target_path.sha256" || true
			fi
		fi
		echo -e "${GREEN}✓ $tool_name installed successfully${NC}"
	else
		echo -e "${YELLOW}Direct download failed, trying alternative methods...${NC}"

		# For hadolint, try alternative installation methods
		if [ "$tool_name" = "hadolint" ]; then
			# Try installing via package managers
			if command -v brew &>/dev/null; then
				echo -e "${YELLOW}Trying Homebrew installation...${NC}"
				if brew install hadolint; then
					# Copy from Homebrew location to target
					local brew_path
					brew_path="$(brew --prefix hadolint)/bin/hadolint"
					if [ -f "$brew_path" ]; then
						cp "$brew_path" "$target_path"
						chmod +x "$target_path"
						echo -e "${GREEN}✓ hadolint installed successfully via Homebrew${NC}"
						return 0
					fi
				fi
			fi

			# Remove invalid pip fallback for hadolint
		fi

		echo -e "${RED}✗ Failed to install $tool_name from $download_url and all fallback methods${NC}"
		exit 1
	fi
}

# Function to install system dependencies for Docker
install_system_deps() {
	if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
		echo -e "${BLUE}Installing system dependencies...${NC}"

		# Update package lists
		if [ "$DRY_RUN" -eq 1 ]; then
			log_info "[DRY-RUN] Would run apt-get update and install system packages"
			return
		fi
		apt-get update

		# Install essential packages
		apt-get install -y --no-install-recommends \
			curl \
			ca-certificates \
			git \
			gnupg

		# Note: bun will be installed via ensure_bun_installed() when needed

		# Install Python packages via apt (more reliable in Docker)
		apt-get install -y --no-install-recommends \
			python3-pip \
			python3-venv

		# Clean up
		apt-get clean
		rm -rf /var/lib/apt/lists/*

		echo -e "${GREEN}✓ System dependencies installed${NC}"
	fi
}

# Ensure cargo-audit build deps are available in local Linux environments
ensure_cargo_audit_deps() {
	if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
		return
	fi
	if ! command -v apt-get &>/dev/null; then
		return
	fi

	# Check which packages are missing
	local missing_pkgs=()
	if ! dpkg-query -W -f='${Status}' libssl-dev 2>/dev/null | grep -q "install ok installed"; then
		missing_pkgs+=("libssl-dev")
	fi
	if ! command -v pkg-config &>/dev/null && ! dpkg-query -W -f='${Status}' pkg-config 2>/dev/null | grep -q "install ok installed"; then
		missing_pkgs+=("pkg-config")
	fi

	if [ ${#missing_pkgs[@]} -eq 0 ]; then
		log_verbose "cargo-audit deps (libssl-dev, pkg-config) already installed"
		return
	fi

	local apt_cmd="apt-get"
	if [ "$(id -u)" -ne 0 ]; then
		if command -v sudo &>/dev/null; then
			apt_cmd="sudo apt-get"
		else
			echo -e "${YELLOW}⚠ cargo-audit deps need apt-get but sudo is unavailable${NC}"
			return
		fi
	fi

	if [ "$DRY_RUN" -eq 1 ]; then
		log_info "[DRY-RUN] Would install ${missing_pkgs[*]} for cargo-audit"
		return
	fi

	echo -e "${BLUE}Installing missing cargo-audit deps: ${missing_pkgs[*]}${NC}"
	$apt_cmd update
	$apt_cmd install -y --no-install-recommends "${missing_pkgs[@]}"
}

# Prepare BIN_DIR for a direct group-installer invocation.
ensure_bin_dir() {
	if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
		BIN_DIR="${BIN_DIR:-/usr/local/bin}"
	else
		BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
		mkdir -p "$BIN_DIR"
	fi
	export BIN_DIR
}
