#!/usr/bin/env bash
set -euo pipefail

# install-tools.sh - Simplified tool installer for lintro
#
# This script installs all external tools required by lintro.
# It uses consistent installation methods and is optimized for Docker environments.
#
# Usage:
#   ./scripts/install-tools.sh [--help] [--dry-run] [--verbose] [--local|--docker]
#                              [--tools tool1,tool2,...]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# SC1091: path is dynamically constructed, file exists at runtime
# shellcheck source=utils.sh disable=SC1091
source "$SCRIPT_DIR/utils.sh"

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

# Show help if requested
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: install-tools.sh [--help] [--dry-run] [--verbose] [--local|--docker]
                       [--tools tool1,tool2,...]

Tool Installation Script
Installs all required linting and formatting tools.

Options:
  --help, -h     Show this help message
  --dry-run      Show what would be done without executing
  --verbose      Enable verbose output
  --local        Install tools locally (default)
  --docker       Install tools system-wide for Docker
  --tools LIST   Only install the specified tools (comma-separated)

This script installs:
  - Ruff (Python linter and formatter)
  - Pydoclint (docstring linter)
  - Black (Python formatter; runs as a post-check in Lintro)
  - Prettier (code formatter)
  - Markdownlint-cli2 (Markdown linter)
  - Yamllint (YAML linter)
  - Hadolint (Dockerfile linter)
  - Actionlint (GitHub Actions workflow linter)
  - Bandit (Python security linter)
  - Mypy (Python static type checker)
  - Clippy (Rust linter; requires Rust toolchain)
  - Rustfmt (Rust formatter; requires Rust toolchain)
  - Cargo-audit (Rust dependency vulnerability scanner; requires Rust toolchain)
  - Cargo-deny (Rust dependency license/advisory checker; requires Rust toolchain)
  - OSV-Scanner (Multi-ecosystem vulnerability scanner)
  - Oxlint (JavaScript/TypeScript linter)
  - Oxfmt (JavaScript/TypeScript formatter)
  - Semgrep (Security scanner)
  - ShellCheck (Shell script linter)
  - shfmt (Shell script formatter)
  - SQLFluff (SQL linter and formatter)
  - Taplo (TOML linter and formatter)
  - TypeScript (TypeScript compiler and type checker)
  - Astro Check (Astro component type checker)
  - Gitleaks (Secret detection scanner)
  - Svelte Check (Svelte component type checker)
  - tsc (TypeScript type checker)
  - Vue-tsc (Vue TypeScript type checker)

Use this script to set up a complete development environment.
EOF
	exit 0
fi

# Global flags
DRY_RUN=0
# Note: VERBOSE may already be set by utils.sh, so use default
VERBOSE="${VERBOSE:-0}"
TOOL_FILTER=""

# Parse flags and collect positional args
POSITIONAL=()
while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--verbose)
		VERBOSE=1
		shift
		;;
	--tools)
		if [[ -z "${2:-}" || "$2" == --* ]]; then
			echo "Error: --tools requires a non-empty comma-separated list of tool names" >&2
			exit 1
		fi
		TOOL_FILTER="$2"
		shift 2
		;;
	--tools=*)
		TOOL_FILTER="${1#*=}"
		if [[ -z "$TOOL_FILTER" ]]; then
			echo "Error: --tools requires a non-empty comma-separated list of tool names" >&2
			exit 1
		fi
		shift
		;;
	--help | -h)
		# Already handled above
		shift
		;;
	*)
		POSITIONAL+=("$1")
		shift
		;;
	esac
done
set -- "${POSITIONAL[@]:-}"

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

# Supported tool names for --tools validation.
# Kept in sync with the should_install blocks and tools_to_verify array.
SUPPORTED_TOOLS=(
	"actionlint" "astro" "bandit" "black" "cargo-audit" "cargo-deny"
	"clippy" "gitleaks" "hadolint" "markdownlint" "markdownlint-cli2" "mypy" "osv-scanner"
	"oxfmt" "oxlint" "phpstan" "prettier" "pydoclint" "ruff" "rustfmt" "semgrep"
	"shellcheck" "shfmt" "sqlfluff" "svelte-check" "taplo" "tsc"
	"vue-tsc" "yamllint"
)

# Validate --tools filter against known tool names (fail-fast on typos).
if [[ -n "$TOOL_FILTER" ]]; then
	IFS=',' read -ra _filter_entries <<<"${TOOL_FILTER//_/-}"
	_invalid=()
	for _entry in "${_filter_entries[@]}"; do
		_entry="${_entry## }"
		_entry="${_entry%% }"
		_found=false
		for _supported in "${SUPPORTED_TOOLS[@]}"; do
			[[ "$_entry" == "$_supported" ]] && {
				_found=true
				break
			}
		done
		[[ "$_found" == false ]] && _invalid+=("$_entry")
	done
	if [[ ${#_invalid[@]} -gt 0 ]]; then
		echo "Error: unknown tool(s) in --tools: ${_invalid[*]}" >&2
		echo "Supported tools: ${SUPPORTED_TOOLS[*]}" >&2
		exit 1
	fi
	unset _filter_entries _invalid _entry _found _supported
fi

# Script-specific logging (prefixed)
install_log() { echo "[install-tools] $*"; }

# Helper function to ensure bun is installed
# Returns 0 on success, non-zero on failure (does not call exit)
ensure_bun_installed() {
	if command -v bun &>/dev/null; then
		return 0
	fi

	echo -e "${YELLOW}bun not found, trying to install...${NC}"

	if [ $DRY_RUN -eq 1 ]; then
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
		if [ $DRY_RUN -eq 1 ]; then
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

# Default to local installation
INSTALL_MODE="${1:-local}"
log_verbose "Selected mode: $INSTALL_MODE"

echo -e "${BLUE}=== Lintro Tool Installer ===${NC}"
echo -e "Mode: ${INSTALL_MODE}"
echo ""

# Determine installation paths based on mode
if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
	BIN_DIR="/usr/local/bin"
	echo -e "${YELLOW}Installing tools system-wide for Docker environment${NC}"
else
	# Local installation - use ~/.local/bin
	BIN_DIR="$HOME/.local/bin"
	mkdir -p "$BIN_DIR"
	echo -e "${YELLOW}Installing tools locally to $BIN_DIR${NC}"
	echo -e "${YELLOW}Make sure $BIN_DIR is in your PATH${NC}"
fi

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
		if [ $DRY_RUN -eq 1 ]; then
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

	if [ $DRY_RUN -eq 1 ]; then
		log_info "[DRY-RUN] Would install ${missing_pkgs[*]} for cargo-audit"
		return
	fi

	echo -e "${BLUE}Installing missing cargo-audit deps: ${missing_pkgs[*]}${NC}"
	$apt_cmd update
	$apt_cmd install -y --no-install-recommends "${missing_pkgs[@]}"
}

# Main installation process
main() {
	echo -e "${YELLOW}Starting tool installation...${NC}"
	echo ""

	# Install system dependencies if in Docker mode
	if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
		install_system_deps
	fi

	# Install hadolint (Docker linting)
	# hadolint with checksum verification when available
	HADOLINT_VERSION=$(get_tool_version "hadolint") || exit 1
	install_tool_curl "hadolint" \
		"https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint"

	# Install gitleaks (secret detection)
	if should_install "gitleaks"; then
		echo -e "${BLUE}Installing gitleaks...${NC}"
		GITLEAKS_VERSION=$(get_tool_version "gitleaks") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install gitleaks v${GITLEAKS_VERSION}"
		elif command -v gitleaks &>/dev/null; then
			echo -e "${GREEN}✓ gitleaks already installed${NC}"
		else
			tmpdir=$(mktemp -d)
			os=$(uname -s | tr '[:upper:]' '[:lower:]')
			arch=$(uname -m)
			case "$arch" in
			x86_64 | amd64) arch_name="x64" ;;
			aarch64 | arm64) arch_name="arm64" ;;
			*) echo -e "${RED}✗ Unsupported architecture: $arch${NC}" && exit 1 ;;
			esac
			tgz_url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${os}_${arch_name}.tar.gz"
			checksum_url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_checksums.txt"
			if download_with_retries "$tgz_url" "$tmpdir/gitleaks.tar.gz" 3; then
				# Require checksum verification before installing
				if ! download_with_retries "$checksum_url" "$tmpdir/checksums.txt" 3; then
					echo -e "${RED}✗ Failed to download checksum file for gitleaks${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				echo -e "${BLUE}Verifying checksum for gitleaks...${NC}"
				expected=$(grep "gitleaks_${GITLEAKS_VERSION}_${os}_${arch_name}.tar.gz" "$tmpdir/checksums.txt" | awk '{print $1}')
				if [ -z "$expected" ]; then
					echo -e "${RED}✗ Checksum entry not found for gitleaks_${GITLEAKS_VERSION}_${os}_${arch_name}.tar.gz in ${tmpdir}/checksums.txt${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				if command -v sha256sum >/dev/null 2>&1; then
					actual=$(sha256sum "$tmpdir/gitleaks.tar.gz" | awk '{print $1}')
				elif command -v shasum >/dev/null 2>&1; then
					actual=$(shasum -a 256 "$tmpdir/gitleaks.tar.gz" | awk '{print $1}')
				else
					echo -e "${RED}✗ Unable to compute checksum: no hash tool found (sha256sum or shasum required)${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				if [ "$expected" != "$actual" ]; then
					echo -e "${RED}✗ Checksum mismatch for gitleaks (expected: $expected, got: $actual)${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				echo -e "${GREEN}✓ Checksum verified${NC}"
				tar -xzf "$tmpdir/gitleaks.tar.gz" -C "$tmpdir"
				cp "$tmpdir/gitleaks" "$BIN_DIR/gitleaks"
				chmod +x "$BIN_DIR/gitleaks"
				echo -e "${GREEN}✓ gitleaks installed successfully${NC}"
			else
				echo -e "${RED}✗ Failed to download gitleaks${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			rm -rf "$tmpdir"
		fi
	fi # gitleaks

	# Install osv-scanner (multi-ecosystem vulnerability scanner)
	if should_install "osv-scanner"; then
		echo -e "${BLUE}Installing osv-scanner...${NC}"
		OSV_SCANNER_VERSION=$(get_tool_version "osv_scanner") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install osv-scanner v${OSV_SCANNER_VERSION}"
		elif command -v osv-scanner &>/dev/null; then
			echo -e "${GREEN}✓ osv-scanner already installed${NC}"
		else
			os=$(uname -s | tr '[:upper:]' '[:lower:]')
			arch=$(uname -m)
			case "$arch" in
			x86_64 | amd64) arch_name="amd64" ;;
			aarch64 | arm64) arch_name="arm64" ;;
			*) echo -e "${RED}✗ Unsupported architecture: $arch${NC}" && exit 1 ;;
			esac
			binary_url="https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_${os}_${arch_name}"
			checksum_url="https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_SHA256SUMS"
			tmpdir=$(mktemp -d)
			if download_with_retries "$binary_url" "$tmpdir/osv-scanner" 3; then
				chmod +x "$tmpdir/osv-scanner"
				# Require checksum verification before installing
				if ! download_with_retries "$checksum_url" "$tmpdir/checksums.txt" 3; then
					echo -e "${RED}✗ Failed to download checksum file for osv-scanner${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				echo -e "${BLUE}Verifying checksum for osv-scanner...${NC}"
				expected=$(grep "osv-scanner_${os}_${arch_name}$" "$tmpdir/checksums.txt" | awk '{print $1}')
				if [ -z "$expected" ]; then
					echo -e "${RED}✗ No checksum entry for osv-scanner_${os}_${arch_name}${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				if command -v sha256sum >/dev/null 2>&1; then
					actual=$(sha256sum "$tmpdir/osv-scanner" | awk '{print $1}')
				elif command -v shasum >/dev/null 2>&1; then
					actual=$(shasum -a 256 "$tmpdir/osv-scanner" | awk '{print $1}')
				else
					echo -e "${RED}✗ No sha256sum or shasum available for checksum verification${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				if [ "$expected" != "$actual" ]; then
					echo -e "${RED}✗ Checksum mismatch for osv-scanner${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				echo -e "${GREEN}✓ Checksum verified${NC}"
				mv "$tmpdir/osv-scanner" "$BIN_DIR/osv-scanner"
				rm -rf "$tmpdir"
				echo -e "${GREEN}✓ osv-scanner installed successfully${NC}"
			else
				echo -e "${RED}✗ Failed to download osv-scanner${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
		fi
	fi # osv-scanner

	if should_install "actionlint"; then
		# Install actionlint (GitHub Actions workflow linter)
		# Prebuilt binaries: https://github.com/rhysd/actionlint/releases
		echo -e "${BLUE}Installing actionlint...${NC}"
		ACTIONLINT_VERSION="v$(get_tool_version "actionlint")" || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install actionlint ${ACTIONLINT_VERSION}"
		else
			tmpdir=$(mktemp -d)
			os=$(uname -s)
			arch=$(uname -m)
			case "$os" in
			Darwin) os_name="darwin" ;;
			Linux) os_name="linux" ;;
			*) os_name="linux" ;;
			esac
			case "$arch" in
			x86_64 | amd64) arch_name="amd64" ;;
			aarch64 | arm64) arch_name="arm64" ;;
			*) echo -e "${RED}✗ Unsupported architecture: $arch${NC}" && exit 1 ;;
			esac
			tgz_url="https://github.com/rhysd/actionlint/releases/download/${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION#v}_${os_name}_${arch_name}.tar.gz"
			tgz_name="actionlint_${ACTIONLINT_VERSION#v}_${os_name}_${arch_name}.tar.gz"
			if download_with_retries "$tgz_url" "$tmpdir/actionlint.tgz" 3; then
				# Require checksum verification before installing
				checksum_url="https://github.com/rhysd/actionlint/releases/download/${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION#v}_checksums.txt"
				if ! download_with_retries "$checksum_url" "$tmpdir/checksums.txt" 3; then
					echo -e "${RED}✗ Failed to download checksum file for actionlint${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				echo -e "${BLUE}Verifying checksum for actionlint...${NC}"
				expected=$(grep "$tgz_name" "$tmpdir/checksums.txt" | awk '{print $1}')
				if [ -z "$expected" ]; then
					echo -e "${RED}✗ Checksum entry not found for ${tgz_name}${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				if command -v sha256sum >/dev/null 2>&1; then
					actual=$(sha256sum "$tmpdir/actionlint.tgz" | awk '{print $1}')
				elif command -v shasum >/dev/null 2>&1; then
					actual=$(shasum -a 256 "$tmpdir/actionlint.tgz" | awk '{print $1}')
				else
					echo -e "${RED}✗ No hash tool found (sha256sum or shasum required)${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				if [ "$expected" != "$actual" ]; then
					echo -e "${RED}✗ Checksum mismatch for actionlint (expected: $expected, got: $actual)${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				echo -e "${GREEN}✓ Checksum verified${NC}"
				if ! tar -xzf "$tmpdir/actionlint.tgz" -C "$tmpdir" >/dev/null 2>&1; then
					echo -e "${RED}✗ Failed to extract actionlint archive${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
				if [ -f "$tmpdir/actionlint" ]; then
					cp "$tmpdir/actionlint" "$BIN_DIR/actionlint"
					chmod +x "$BIN_DIR/actionlint"
					echo -e "${GREEN}✓ actionlint installed successfully${NC}"
				else
					echo -e "${RED}✗ Could not find extracted actionlint binary${NC}"
					rm -rf "$tmpdir"
					exit 1
				fi
			else
				echo -e "${RED}✗ Failed to download actionlint prebuilt binary${NC}"
				rm -rf "$tmpdir"
				exit 1
			fi
			rm -rf "$tmpdir" || true
		fi
	fi # actionlint

	if should_install "shfmt"; then
		# Install shfmt (shell script formatter)
		echo -e "${BLUE}Installing shfmt...${NC}"
		SHFMT_VERSION=$(get_tool_version "shfmt") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install shfmt v${SHFMT_VERSION}"
		elif command -v shfmt &>/dev/null; then
			echo -e "${GREEN}✓ shfmt already installed${NC}"
		else
			os=$(uname -s | tr '[:upper:]' '[:lower:]')
			arch=$(uname -m)
			case "$arch" in
			x86_64 | amd64) arch="amd64" ;;
			aarch64 | arm64) arch="arm64" ;;
			esac
			binary_url="https://github.com/mvdan/sh/releases/download/v${SHFMT_VERSION}/shfmt_v${SHFMT_VERSION}_${os}_${arch}"
			if download_with_retries "$binary_url" "$BIN_DIR/shfmt" 3; then
				chmod +x "$BIN_DIR/shfmt"
				echo -e "${GREEN}✓ shfmt installed successfully${NC}"
			else
				echo -e "${RED}✗ Failed to download shfmt${NC}"
				exit 1
			fi
		fi
	fi # shfmt

	if should_install "phpstan"; then
		# Install PHPStan (PHP static analysis) as a standalone PHAR.
		# PHPStan requires a PHP runtime at execution time; PHP itself is
		# provided by the system package manager (apt php-cli in Docker,
		# `brew install php` locally).
		echo -e "${BLUE}Installing phpstan...${NC}"
		PHPSTAN_VERSION=$(get_tool_version "phpstan") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install phpstan v${PHPSTAN_VERSION}"
		elif command -v phpstan &>/dev/null && command -v php &>/dev/null; then
			echo -e "${GREEN}✓ phpstan already installed${NC}"
		elif ! command -v php &>/dev/null; then
			# The PHAR cannot run (or be verified) without a PHP interpreter;
			# installing it anyway would leave a broken binary on PATH and a
			# failing verification. An explicit request for phpstan without
			# its runtime is an error, not a skip.
			echo -e "${RED}✗ Cannot install phpstan: no 'php' interpreter on PATH." \
				"Install PHP (apt install php-cli / brew install php) and re-run.${NC}"
			exit 1
		else
			phar_url="https://github.com/phpstan/phpstan/releases/download/${PHPSTAN_VERSION}/phpstan.phar"
			if download_with_retries "$phar_url" "$BIN_DIR/phpstan" 3; then
				chmod +x "$BIN_DIR/phpstan"
				echo -e "${GREEN}✓ phpstan installed successfully${NC}"
			else
				echo -e "${RED}✗ Failed to download phpstan${NC}"
				exit 1
			fi
		fi
	fi # phpstan

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

		if [ $DRY_RUN -eq 1 ]; then
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
			installed_version=$(rustc --version 2>/dev/null | awk '{print $2}')
			if [ -z "$installed_version" ] || [ "$installed_version" != "$RUST_TOOLCHAIN_VERSION" ]; then
				echo -e "${RED}✗ rustc version mismatch (expected ${RUST_TOOLCHAIN_VERSION}, got ${installed_version:-unknown})${NC}"
				exit 1
			fi
		fi
		echo -e "${GREEN}✓ ${component} installed successfully${NC}"
	}

	if should_install "rustfmt"; then
		echo -e "${BLUE}Installing rustfmt...${NC}"
		ensure_rust_toolchain "rustfmt"
	fi # rustfmt

	if should_install "clippy"; then
		echo -e "${BLUE}Installing clippy...${NC}"
		# clippy is invoked via cargo, verify with cargo clippy
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install clippy"
		elif command -v cargo &>/dev/null && cargo clippy --version &>/dev/null; then
			echo -e "${GREEN}✓ clippy already installed${NC}"
		else
			ensure_rust_toolchain "clippy"
		fi
	fi # clippy

	if should_install "cargo-audit"; then
		# Install cargo-audit (Rust dependency vulnerability scanner)
		# Prefer pre-built binary from cargo-quickinstall to avoid 20+ minute compile times
		echo -e "${BLUE}Installing cargo-audit...${NC}"
		CARGO_AUDIT_VERSION=$(get_tool_version "cargo_audit") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install cargo-audit==${CARGO_AUDIT_VERSION}"
		elif command -v cargo-audit &>/dev/null; then
			echo -e "${GREEN}✓ cargo-audit already installed${NC}"
		else
			cargo_audit_installed=false
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
	fi # cargo-audit

	if should_install "cargo-deny"; then
		# Install cargo-deny (Rust dependency license/advisory checker)
		# Prefer pre-built binary from cargo-quickinstall to avoid long compile times
		echo -e "${BLUE}Installing cargo-deny...${NC}"
		CARGO_DENY_VERSION=$(get_tool_version "cargo_deny") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install cargo-deny==${CARGO_DENY_VERSION}"
		elif command -v cargo-deny &>/dev/null; then
			echo -e "${GREEN}✓ cargo-deny already installed${NC}"
		else
			cargo_deny_installed=false
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
	fi # cargo-deny

	if should_install "ruff"; then
		# Install ruff (Python linting and formatting)
		echo -e "${BLUE}Installing ruff...${NC}"
		RUFF_VERSION=$(get_tool_version "ruff") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install ruff==${RUFF_VERSION}"
		elif install_python_package "ruff" "$RUFF_VERSION"; then
			echo -e "${GREEN}✓ ruff installed successfully${NC}"
		else
			if command -v brew &>/dev/null; then
				echo -e "${YELLOW}Trying Homebrew for ruff...${NC}"
				if [ $DRY_RUN -eq 1 ]; then
					log_info "[DRY-RUN] Would install ruff via brew"
				else
					brew install ruff || {
						echo -e "${RED}✗ Failed to install ruff via Homebrew${NC}"
						exit 1
					}
				fi
				echo -e "${GREEN}✓ ruff installed successfully via Homebrew${NC}"
			else
				echo -e "${RED}✗ Cannot install ruff automatically; please install via your package manager.${NC}"
				exit 1
			fi
		fi
	fi # ruff

	if should_install "black"; then
		# Install black (Python code formatter)
		echo -e "${BLUE}Installing black...${NC}"
		BLACK_VERSION=$(get_tool_version "black") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install black==${BLACK_VERSION}"
		elif install_python_package "black" "$BLACK_VERSION"; then
			echo -e "${GREEN}✓ black installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install black${NC}"
			exit 1
		fi
	fi # black

	if should_install "bandit"; then
		# Install bandit (Python security linter)
		echo -e "${BLUE}Installing bandit...${NC}"
		BANDIT_VERSION=$(get_tool_version "bandit") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install bandit==${BANDIT_VERSION}"
		elif install_python_package "bandit" "$BANDIT_VERSION"; then
			echo -e "${GREEN}✓ bandit installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install bandit${NC}"
			exit 1
		fi
	fi # bandit

	if should_install "mypy"; then
		# Install mypy (Python type checker)
		echo -e "${BLUE}Installing mypy...${NC}"
		MYPY_VERSION=$(get_tool_version "mypy") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install mypy==${MYPY_VERSION}"
		elif install_python_package "mypy" "$MYPY_VERSION"; then
			echo -e "${GREEN}✓ mypy installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install mypy${NC}"
			exit 1
		fi
	fi # mypy

	if should_install "prettier"; then
		# Install prettier via bun (JavaScript/JSON formatting)
		echo -e "${BLUE}Installing prettier...${NC}"

		# Ensure bun is available
		if ! ensure_bun_installed; then
			exit 1
		fi

		# Read prettier version from _tool_versions.py (single source of truth)
		PRETTIER_VERSION=$(get_tool_version "prettier") || exit 1

		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install prettier@${PRETTIER_VERSION} globally via bun"
		elif bun add -g "prettier@${PRETTIER_VERSION}"; then
			echo -e "${GREEN}✓ prettier@${PRETTIER_VERSION} installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install prettier${NC}"
			exit 1
		fi
	fi # prettier

	if should_install "markdownlint" || should_install "markdownlint-cli2"; then
		# Install markdownlint-cli2 via bun (Markdown linting)
		echo -e "${BLUE}Installing markdownlint-cli2...${NC}"

		# Ensure bun is available (should already be installed for prettier)
		if ! ensure_bun_installed; then
			exit 1
		fi

		# Read markdownlint-cli2 version from _tool_versions.py (single source of truth)
		# Uses package alias: "markdownlint-cli2" -> ToolName.MARKDOWNLINT
		MARKDOWNLINT_VERSION=$(get_tool_version "markdownlint-cli2") || exit 1

		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install markdownlint-cli2@${MARKDOWNLINT_VERSION} globally via bun"
		elif bun add -g "markdownlint-cli2@${MARKDOWNLINT_VERSION}"; then
			echo -e "${GREEN}✓ markdownlint-cli2@${MARKDOWNLINT_VERSION} installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install markdownlint-cli2${NC}"
			exit 1
		fi
	fi # markdownlint

	if should_install "semgrep"; then
		# Install semgrep (security scanner)
		echo -e "${BLUE}Installing semgrep...${NC}"
		SEMGREP_VERSION=$(get_tool_version "semgrep") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install semgrep==${SEMGREP_VERSION}"
		elif install_python_package "semgrep" "$SEMGREP_VERSION"; then
			echo -e "${GREEN}✓ semgrep installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install semgrep${NC}"
			exit 1
		fi
	fi # semgrep

	if should_install "shellcheck"; then
		# Install shellcheck (shell script linter)
		echo -e "${BLUE}Installing shellcheck...${NC}"
		SHELLCHECK_VERSION=$(get_tool_version "shellcheck") || exit 1

		# Helper function for shellcheck binary installation
		install_shellcheck_binary() {
			local tmpdir
			tmpdir=$(mktemp -d)
			local os arch tar_url
			os=$(uname -s | tr '[:upper:]' '[:lower:]')
			arch=$(uname -m)
			case "$arch" in
			x86_64 | amd64) arch="x86_64" ;;
			aarch64 | arm64) arch="aarch64" ;;
			esac
			tar_url="https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.${os}.${arch}.tar.xz"
			if download_with_retries "$tar_url" "$tmpdir/shellcheck.tar.xz" 3; then
				tar -xJf "$tmpdir/shellcheck.tar.xz" -C "$tmpdir"
				cp "$tmpdir/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" "$BIN_DIR/shellcheck"
				chmod +x "$BIN_DIR/shellcheck"
				rm -rf "$tmpdir"
				return 0
			else
				rm -rf "$tmpdir"
				return 1
			fi
		}

		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install shellcheck v${SHELLCHECK_VERSION}"
		elif command -v shellcheck &>/dev/null; then
			# Check if installed version meets minimum requirement
			installed_version=$(shellcheck --version 2>/dev/null | grep -oE 'version: [0-9]+\.[0-9]+\.[0-9]+' | cut -d' ' -f2)
			if [ -n "$installed_version" ]; then
				# Compare versions using portable version_ge function from utils.sh
				if version_ge "$installed_version" "$SHELLCHECK_VERSION"; then
					echo -e "${GREEN}✓ shellcheck v${installed_version} already installed (>= v${SHELLCHECK_VERSION})${NC}"
				else
					echo -e "${YELLOW}⚠ shellcheck v${installed_version} is older than required v${SHELLCHECK_VERSION}, upgrading...${NC}"
					if install_shellcheck_binary; then
						echo -e "${GREEN}✓ shellcheck upgraded to v${SHELLCHECK_VERSION}${NC}"
					else
						echo -e "${RED}✗ Failed to download shellcheck${NC}"
						exit 1
					fi
				fi
			else
				# Could not parse version, treat as not installed
				echo -e "${YELLOW}⚠ Could not determine shellcheck version, installing v${SHELLCHECK_VERSION}...${NC}"
				if install_shellcheck_binary; then
					echo -e "${GREEN}✓ shellcheck installed successfully${NC}"
				else
					echo -e "${RED}✗ Failed to download shellcheck${NC}"
					exit 1
				fi
			fi
		else
			if install_shellcheck_binary; then
				echo -e "${GREEN}✓ shellcheck installed successfully${NC}"
			else
				echo -e "${RED}✗ Failed to download shellcheck${NC}"
				exit 1
			fi
		fi
	fi # end shellcheck block

	if should_install "oxlint"; then
		# Install oxlint via bun (JavaScript/TypeScript linting)
		echo -e "${BLUE}Installing oxlint...${NC}"

		# Ensure bun is available (should already be installed for prettier)
		if ! ensure_bun_installed; then
			exit 1
		fi

		OXLINT_VERSION=$(get_tool_version "oxlint") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install oxlint@${OXLINT_VERSION} globally via bun"
		elif bun add -g "oxlint@${OXLINT_VERSION}"; then
			echo -e "${GREEN}✓ oxlint@${OXLINT_VERSION} installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install oxlint${NC}"
			exit 1
		fi
	fi # oxlint

	if should_install "oxfmt"; then
		# Install oxfmt via bun (JavaScript/TypeScript formatting)
		echo -e "${BLUE}Installing oxfmt...${NC}"

		if ! ensure_bun_installed; then
			exit 1
		fi

		OXFMT_VERSION=$(get_tool_version "oxfmt") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install oxfmt@${OXFMT_VERSION} globally via bun"
		elif bun add -g "oxfmt@${OXFMT_VERSION}"; then
			echo -e "${GREEN}✓ oxfmt@${OXFMT_VERSION} installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install oxfmt${NC}"
			exit 1
		fi
	fi # oxfmt

	if should_install "yamllint"; then
		# Install yamllint (Python package)
		echo -e "${BLUE}Installing yamllint...${NC}"
		YAMLLINT_VERSION=$(get_tool_version "yamllint") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install yamllint==${YAMLLINT_VERSION}"
		elif install_python_package "yamllint" "$YAMLLINT_VERSION"; then
			echo -e "${GREEN}✓ yamllint installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install yamllint${NC}"
			exit 1
		fi
	fi # yamllint

	if should_install "pydoclint"; then
		# Install pydoclint (Python docstring linter)
		echo -e "${BLUE}Installing pydoclint...${NC}"
		PYDOCLINT_VERSION=$(get_tool_version "pydoclint") || exit 1

		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install pydoclint==${PYDOCLINT_VERSION}"
		elif install_python_package "pydoclint" "$PYDOCLINT_VERSION"; then
			echo -e "${GREEN}✓ pydoclint installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install pydoclint${NC}"
			exit 1
		fi
	fi # pydoclint

	if should_install "sqlfluff"; then
		# Install sqlfluff (SQL linter and formatter)
		echo -e "${BLUE}Installing sqlfluff...${NC}"
		SQLFLUFF_VERSION=$(get_tool_version "sqlfluff") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install sqlfluff==${SQLFLUFF_VERSION}"
		elif install_python_package "sqlfluff" "$SQLFLUFF_VERSION"; then
			echo -e "${GREEN}✓ sqlfluff installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install sqlfluff${NC}"
			exit 1
		fi
	fi # sqlfluff

	if should_install "taplo"; then
		# Install taplo (TOML linter and formatter)
		echo -e "${BLUE}Installing taplo...${NC}"
		TAPLO_VERSION=$(get_tool_version "taplo") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install taplo v${TAPLO_VERSION}"
		elif command -v taplo &>/dev/null; then
			echo -e "${GREEN}✓ taplo already installed${NC}"
		else
			taplo_installed=false
			tmpdir=$(mktemp -d)
			os=$(uname -s | tr '[:upper:]' '[:lower:]')
			arch=$(uname -m)
			case "$arch" in
			x86_64 | amd64) arch="x86_64" ;;
			aarch64 | arm64) arch="aarch64" ;;
			esac
			# taplo releases use format: taplo-{os}-{arch}.gz (changed from taplo-full- in v0.9+)
			gz_url="https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-${os}-${arch}.gz"
			# Check if GitHub release exists before attempting download
			if curl -sfIL "$gz_url" >/dev/null 2>&1; then
				if download_with_retries "$gz_url" "$tmpdir/taplo.gz" 3; then
					# Require checksum verification before installing
					checksum_url="${gz_url}.sha256"
					checksum_ok=false
					if download_with_retries "$checksum_url" "$tmpdir/taplo.gz.sha256" 3; then
						echo -e "${BLUE}Verifying checksum for taplo...${NC}"
						expected=$(awk '{print $1}' "$tmpdir/taplo.gz.sha256" | head -n1)
						if command -v sha256sum >/dev/null 2>&1; then
							actual=$(sha256sum "$tmpdir/taplo.gz" | awk '{print $1}')
						elif command -v shasum >/dev/null 2>&1; then
							actual=$(shasum -a 256 "$tmpdir/taplo.gz" | awk '{print $1}')
						else
							echo -e "${RED}✗ No sha256sum or shasum available for checksum verification${NC}"
						fi
						if [ "$expected" = "$actual" ]; then
							echo -e "${GREEN}✓ Checksum verified${NC}"
							checksum_ok=true
						else
							echo -e "${RED}✗ Checksum mismatch for taplo (expected: $expected, got: $actual)${NC}"
						fi
						rm -f "$tmpdir/taplo.gz.sha256" || true
					else
						echo -e "${RED}✗ Failed to download checksum file for taplo${NC}"
					fi
					# Only install if checksum verification passed
					if [ "$checksum_ok" = true ]; then
						gunzip -c "$tmpdir/taplo.gz" >"$BIN_DIR/taplo"
						chmod +x "$BIN_DIR/taplo"
						echo -e "${GREEN}✓ taplo installed successfully${NC}"
						taplo_installed=true
					fi
				fi
			else
				echo -e "${YELLOW}⚠ GitHub release for taplo v${TAPLO_VERSION} not available${NC}"
			fi
			rm -rf "$tmpdir"

			# Fallback to cargo if binary download failed
			if [ "$taplo_installed" = false ]; then
				echo -e "${BLUE}Attempting fallback installation via cargo...${NC}"
				if command -v cargo &>/dev/null; then
					echo -e "${BLUE}Installing taplo via cargo...${NC}"
					if cargo install taplo-cli --locked --version "$TAPLO_VERSION"; then
						# Derive cargo bin directory from CARGO_HOME or default
						cargo_bin="${CARGO_HOME:-$HOME/.cargo}/bin"
						# Check for executable taplo in cargo bin, fall back to PATH
						if [ -x "$cargo_bin/taplo" ]; then
							cp "$cargo_bin/taplo" "$BIN_DIR/taplo"
							chmod +x "$BIN_DIR/taplo"
							echo -e "${GREEN}✓ taplo installed via cargo${NC}"
							taplo_installed=true
						elif command -v taplo &>/dev/null; then
							echo -e "${GREEN}✓ taplo installed via cargo (found on PATH)${NC}"
							taplo_installed=true
						fi
					fi
				fi
			fi

			if [ "$taplo_installed" = false ]; then
				echo -e "${RED}✗ Failed to install taplo${NC}"
				exit 1
			fi
		fi
	fi # taplo

	if should_install "tsc"; then
		# Install typescript via bun (TypeScript compiler)
		echo -e "${BLUE}Installing typescript...${NC}"

		if ! ensure_bun_installed; then
			exit 1
		fi

		TYPESCRIPT_VERSION=$(get_tool_version "typescript") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install typescript@${TYPESCRIPT_VERSION} globally via bun"
		elif bun add -g "typescript@${TYPESCRIPT_VERSION}"; then
			echo -e "${GREEN}✓ typescript@${TYPESCRIPT_VERSION} installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install typescript${NC}"
			exit 1
		fi
	fi # tsc

	if should_install "astro"; then
		# Install astro and @astrojs/check via bun (Astro type checking)
		echo -e "${BLUE}Installing astro and @astrojs/check...${NC}"

		if ! ensure_bun_installed; then
			exit 1
		fi

		ASTRO_VERSION=$(get_tool_version "astro") || exit 1
		ASTRO_CHECK_VERSION=$(get_tool_version "@astrojs/check") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install astro@${ASTRO_VERSION} and @astrojs/check@${ASTRO_CHECK_VERSION} globally via bun"
		elif bun add -g "astro@${ASTRO_VERSION}" "@astrojs/check@${ASTRO_CHECK_VERSION}"; then
			echo -e "${GREEN}✓ astro@${ASTRO_VERSION} and @astrojs/check@${ASTRO_CHECK_VERSION} installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install astro and @astrojs/check${NC}"
			exit 1
		fi
	fi # astro

	if should_install "svelte-check"; then
		# Install svelte-check via bun (Svelte type checking)
		echo -e "${BLUE}Installing svelte-check...${NC}"

		if ! ensure_bun_installed; then
			exit 1
		fi

		SVELTE_CHECK_VERSION=$(get_tool_version "svelte-check") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install svelte-check@${SVELTE_CHECK_VERSION} globally via bun"
		elif bun add -g "svelte-check@${SVELTE_CHECK_VERSION}"; then
			echo -e "${GREEN}✓ svelte-check@${SVELTE_CHECK_VERSION} installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install svelte-check${NC}"
			exit 1
		fi
	fi # svelte-check

	if should_install "vue-tsc"; then
		# Install vue-tsc via bun (Vue TypeScript type checking)
		echo -e "${BLUE}Installing vue-tsc...${NC}"

		if ! ensure_bun_installed; then
			exit 1
		fi

		VUE_TSC_VERSION=$(get_tool_version "vue-tsc") || exit 1
		if [ $DRY_RUN -eq 1 ]; then
			log_info "[DRY-RUN] Would install vue-tsc@${VUE_TSC_VERSION} globally via bun"
		elif bun add -g "vue-tsc@${VUE_TSC_VERSION}"; then
			echo -e "${GREEN}✓ vue-tsc@${VUE_TSC_VERSION} installed successfully${NC}"
		else
			echo -e "${RED}✗ Failed to install vue-tsc${NC}"
			exit 1
		fi

	fi # vue-tsc

	echo ""
	echo -e "${GREEN}=== Installation Complete! ===${NC}"
	echo ""
	# Build summary of installed tools (only show requested tools when --tools is set)
	declare -A TOOL_DESCRIPTIONS=(
		["actionlint"]="GitHub Actions linting"
		["astro"]="Astro type checking"
		["bandit"]="Python security checks"
		["black"]="Python formatting"
		["cargo-audit"]="Rust dependency vulnerability scanning"
		["cargo-deny"]="Rust dependency license/advisory checking"
		["clippy"]="Rust linting"
		["gitleaks"]="Secret detection"
		["hadolint"]="Docker linting"
		["markdownlint"]="Markdown linting"
		["mypy"]="Python type checking"
		["osv-scanner"]="Multi-ecosystem vulnerability scanning"
		["oxfmt"]="JavaScript/TypeScript formatting"
		["oxlint"]="JavaScript/TypeScript linting"
		["phpstan"]="PHP static analysis"
		["prettier"]="JavaScript/JSON formatting"
		["pydoclint"]="Python docstring validation"
		["ruff"]="Python linting and formatting"
		["rustfmt"]="Rust formatting"
		["semgrep"]="Security scanning"
		["shellcheck"]="Shell script linting"
		["shfmt"]="Shell script formatting"
		["sqlfluff"]="SQL linting and formatting"
		["svelte-check"]="Svelte type checking"
		["taplo"]="TOML linting and formatting"
		["tsc"]="TypeScript type checking"
		["vue-tsc"]="Vue TypeScript type checking"
		["yamllint"]="YAML linting"
	)
	echo -e "${YELLOW}Installed tools:${NC}"
	for tool in $(echo "${!TOOL_DESCRIPTIONS[@]}" | tr ' ' '\n' | sort); do
		if should_install "$tool"; then
			echo "  - ${tool} (${TOOL_DESCRIPTIONS[$tool]})"
		fi
	done
	echo ""

	# Verify installations
	echo -e "${YELLOW}Verifying installations...${NC}"

	tools_to_verify=("actionlint" "astro" "bandit" "black" "cargo-audit" "cargo-deny" "clippy" "rustfmt" "gitleaks" "hadolint" "markdownlint-cli2" "mypy" "osv-scanner" "oxfmt" "oxlint" "phpstan" "prettier" "pydoclint" "ruff" "semgrep" "shellcheck" "shfmt" "sqlfluff" "svelte-check" "taplo" "tsc" "vue-tsc" "yamllint")

	# Filter verification list when --tools is set.
	# Map aliases so e.g. --tools markdownlint verifies markdownlint-cli2.
	if [[ -n "$TOOL_FILTER" ]]; then
		filtered=()
		for tool in "${tools_to_verify[@]}"; do
			if should_install "$tool"; then
				filtered+=("$tool")
			# markdownlint alias → markdownlint-cli2 verification
			elif [[ "$tool" == "markdownlint-cli2" ]] && should_install "markdownlint"; then
				filtered+=("$tool")
			fi
		done
		tools_to_verify=("${filtered[@]}")
	fi

	for tool in "${tools_to_verify[@]}"; do
		if [ "$tool" = "clippy" ]; then
			# Clippy is invoked through cargo
			if command -v cargo &>/dev/null && cargo clippy --version &>/dev/null; then
				version=$(cargo clippy --version 2>/dev/null || echo "installed")
				echo -e "${GREEN}✓ clippy: $version${NC}"
			else
				echo -e "${RED}✗ clippy: not found (requires cargo)${NC}"
			fi
		elif [ "$tool" = "rustfmt" ]; then
			# Rustfmt is a rustup component
			if command -v rustfmt &>/dev/null; then
				version=$(rustfmt --version 2>/dev/null || echo "installed")
				echo -e "${GREEN}✓ rustfmt: $version${NC}"
			else
				echo -e "${RED}✗ rustfmt: not found (requires rustup component add rustfmt)${NC}"
			fi
		elif command -v "$tool" &>/dev/null; then
			version=$("$tool" --version 2>/dev/null || echo "installed")
			echo -e "${GREEN}✓ $tool: $version${NC}"
		else
			echo -e "${RED}✗ $tool: not found in PATH${NC}"
		fi
	done

	if [ "$INSTALL_MODE" != "--docker" ] && [ "$INSTALL_MODE" != "docker" ]; then
		echo ""
		echo -e "${YELLOW}Local installation notes:${NC}"
		echo "  - Make sure $BIN_DIR is in your PATH"
		echo "  - Run 'uv sync --dev' to install Python dependencies"
		echo "  - Use './scripts/local/local-lintro.sh' or 'uv run lintro' to run lintro"
	fi
}

# Run main function
main "$@"
