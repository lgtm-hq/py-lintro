#!/usr/bin/env bash
set -euo pipefail

# install-tools.sh - Orchestrator for lintro external tool installation
#
# Parses flags, exports shared globals, and invokes modular group installers
# under scripts/utils/installers/.
#
# Usage:
#   ./scripts/utils/install-tools.sh [--help] [--dry-run] [--verbose] [--local|--docker]
#                                    [--tools tool1,tool2,...]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALLERS_DIR="$SCRIPT_DIR/installers"

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
  - dotenv-linter (.env file linter and fixer)
  - SQLFluff (SQL linter and formatter)
  - Taplo (TOML linter and formatter)
  - Vale (Prose/documentation linter)
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

# Supported tool names for --tools validation.
# Kept in sync with the should_install blocks and tools_to_verify array.
SUPPORTED_TOOLS=(
	"actionlint" "astro" "bandit" "black" "cargo-audit" "cargo-deny"
	"clippy" "commitlint" "dotenv-linter" "gitleaks" "hadolint" "markdownlint" "markdownlint-cli2" "mypy" "osv-scanner"
	"oxfmt" "oxlint" "pip-audit" "prettier" "pydoclint" "ruff" "rustfmt" "semgrep"
	"shellcheck" "shfmt" "sqlfluff" "stylelint" "svelte-check" "taplo" "tsc"
	"vale" "vue-tsc" "yamllint"
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

# Default to local installation
INSTALL_MODE="${1:-local}"

# Determine installation paths based on mode
if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
	BIN_DIR="/usr/local/bin"
else
	# Local installation - use ~/.local/bin
	BIN_DIR="$HOME/.local/bin"
	mkdir -p "$BIN_DIR"
fi

# Export globals for group installers
export PROJECT_ROOT SCRIPT_DIR INSTALLERS_DIR
export DRY_RUN VERBOSE TOOL_FILTER INSTALL_MODE BIN_DIR

# Load shared helpers and group installers
# shellcheck source=installers/_helpers.sh disable=SC1091
source "$INSTALLERS_DIR/_helpers.sh"
# shellcheck source=installers/binary-tools.sh disable=SC1091
source "$INSTALLERS_DIR/binary-tools.sh"
# shellcheck source=installers/rust-tools.sh disable=SC1091
source "$INSTALLERS_DIR/rust-tools.sh"
# shellcheck source=installers/python-tools.sh disable=SC1091
source "$INSTALLERS_DIR/python-tools.sh"
# shellcheck source=installers/node-tools.sh disable=SC1091
source "$INSTALLERS_DIR/node-tools.sh"

log_verbose "Selected mode: $INSTALL_MODE"

echo -e "${BLUE}=== Lintro Tool Installer ===${NC}"
echo -e "Mode: ${INSTALL_MODE}"
echo ""

if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
	echo -e "${YELLOW}Installing tools system-wide for Docker environment${NC}"
else
	echo -e "${YELLOW}Installing tools locally to $BIN_DIR${NC}"
	echo -e "${YELLOW}Make sure $BIN_DIR is in your PATH${NC}"
fi

# Main installation process
main() {
	echo -e "${YELLOW}Starting tool installation...${NC}"
	echo ""

	# Install system dependencies if in Docker mode
	if [ "$INSTALL_MODE" = "--docker" ] || [ "$INSTALL_MODE" = "docker" ]; then
		install_system_deps
	fi

	# Rust before binary tools: taplo's GitHub checksum often 404s and falls
	# back to `cargo install`, which needs rustup/cargo already on PATH.
	install_rust_tools
	install_binary_tools
	install_python_tools
	install_node_tools

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
		["dotenv-linter"]=".env file linting and fixing"
		["gitleaks"]="Secret detection"
		["hadolint"]="Docker linting"
		["markdownlint"]="Markdown linting"
		["mypy"]="Python type checking"
		["osv-scanner"]="Multi-ecosystem vulnerability scanning"
		["oxfmt"]="JavaScript/TypeScript formatting"
		["oxlint"]="JavaScript/TypeScript linting"
		["pip-audit"]="Python dependency vulnerability scanning"
		["prettier"]="JavaScript/JSON formatting"
		["pydoclint"]="Python docstring validation"
		["ruff"]="Python linting and formatting"
		["rustfmt"]="Rust formatting"
		["semgrep"]="Security scanning"
		["shellcheck"]="Shell script linting"
		["shfmt"]="Shell script formatting"
		["sqlfluff"]="SQL linting and formatting"
		["stylelint"]="CSS/SCSS/Less linting"
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

	local tools_to_verify
	tools_to_verify=("actionlint" "astro" "bandit" "black" "cargo-audit" "cargo-deny" "clippy" "commitlint" "dotenv-linter" "gitleaks" "hadolint" "markdownlint-cli2" "mypy" "osv-scanner" "oxfmt" "oxlint" "pip-audit" "prettier" "pydoclint" "ruff" "rustfmt" "semgrep" "shellcheck" "shfmt" "sqlfluff" "stylelint" "svelte-check" "taplo" "tsc" "vale" "vue-tsc" "yamllint")

	# Filter verification list when --tools is set.
	# Map aliases so e.g. --tools markdownlint verifies markdownlint-cli2.
	if [[ -n "$TOOL_FILTER" ]]; then
		local filtered=()
		local tool
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

	local version
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
