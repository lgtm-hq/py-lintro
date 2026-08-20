#!/usr/bin/env bash
set -euo pipefail

# local-lintro.sh - Enhanced local lintro runner
#
# This script handles the complete setup and execution of lintro locally.
# It automatically installs missing tools and sets up the environment.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../utils/utils.sh"

echo -e "${BLUE}=== Lintro Local Runner ===${NC}"

# Load environment variables from .env file if it exists
if [ -f .env ]; then
	echo -e "${YELLOW}Loading environment variables from .env file...${NC}"
	# SC2046: word splitting is intentional for env var export
	# shellcheck disable=SC2046
	export $(grep -v '^#' .env | xargs)
fi

# Enable unsafe fixes for local development
# This allows C4 and SIM rules to be auto-fixed during development
export RUFF_UNSAFE_FIXES=true
echo -e "${YELLOW}Enabled unsafe fixes for local development (RUFF_UNSAFE_FIXES=true)${NC}"

# install-tools.sh --local drops binaries into ~/.local/bin. Its own PATH
# export dies with that subprocess, so add the directory here too — otherwise
# a tool this script just installed is still invisible to the lintro run below
# on a fresh shell that does not already have ~/.local/bin on PATH.
add_local_bin_to_path() {
	local local_bin="$HOME/.local/bin"
	case ":$PATH:" in
	*":$local_bin:"*) ;;
	*)
		export PATH="$local_bin:$PATH"
		echo -e "${YELLOW}Added $local_bin to PATH for this run.${NC}"
		echo -e "${YELLOW}Add it to your shell profile to make this permanent:${NC}"
		echo -e "${YELLOW}    export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
		;;
	esac
}

# Function to check if tools are installed
check_and_install_tools() {
	echo -e "${BLUE}Checking tool availability...${NC}"

	# Look in the installer's target directory before deciding anything is
	# missing: a previously installed tool there is invisible to `command -v`
	# on a shell that never added ~/.local/bin to PATH.
	add_local_bin_to_path

	# Check for required tools
	local missing_tools=()

	# Check system tools
	if ! command -v hadolint &>/dev/null; then
		missing_tools+=("hadolint")
	fi

	if ! command -v prettier &>/dev/null; then
		missing_tools+=("prettier")
	fi

	if ! command -v ruff &>/dev/null; then
		missing_tools+=("ruff")
	fi

	if ! command -v uv &>/dev/null; then
		missing_tools+=("uv")
	fi

	# typos runs in the default toolset, so a missing binary degrades every
	# subsequent `lintro check`/`format` run rather than just one tool.
	if ! command -v typos &>/dev/null; then
		missing_tools+=("typos")
	fi

	# If tools are missing, offer to install them (or auto-install with --yes)
	if [ ${#missing_tools[@]} -gt 0 ]; then
		echo -e "${YELLOW}Missing tools: ${missing_tools[*]}${NC}"
		if [ "${ASSUME_YES:-0}" = "1" ]; then
			echo -e "${BLUE}Installing missing tools (non-interactive)...${NC}"
			if [ -f "./scripts/utils/install-tools.sh" ]; then
				./scripts/utils/install-tools.sh --local
				add_local_bin_to_path
			else
				echo -e "${RED}Error: scripts/utils/install-tools.sh not found${NC}"
				echo -e "${YELLOW}Please run the installation script manually or ensure tools are installed${NC}"
				exit 1
			fi
		else
			echo -e "${YELLOW}Would you like to install them automatically? (y/n)${NC}"
			read -r response
			if [[ "$response" =~ ^[Yy]$ ]]; then
				echo -e "${BLUE}Installing missing tools...${NC}"
				if [ -f "./scripts/utils/install-tools.sh" ]; then
					./scripts/utils/install-tools.sh --local
					add_local_bin_to_path
				else
					echo -e "${RED}Error: scripts/utils/install-tools.sh not found${NC}"
					echo -e "${YELLOW}Please run the installation script manually or ensure tools are installed${NC}"
					exit 1
				fi
			else
				echo -e "${YELLOW}Continuing without installing tools. Some lintro features may not work.${NC}"
			fi
		fi
	else
		echo -e "${GREEN}✓ All tools are available${NC}"
	fi
}

# Function to setup Python environment
setup_python_env() {
	echo -e "${BLUE}Setting up Python environment...${NC}"

	# Check if we're running in Docker (environment should be ready)
	if [ -n "${RUNNING_IN_DOCKER:-}" ] || [ -f "/.dockerenv" ]; then
		echo -e "${GREEN}✓ Running in Docker - environment already set up${NC}"
		# In Docker, we don't need to create a virtual environment
		# The dependencies are already installed in the container
		return 0
	fi

	echo -e "${YELLOW}Setting up Python environment with uv...${NC}"

	# Ensure uv is available
	if ! command -v uv &>/dev/null; then
		echo -e "${RED}Error: uv is required but not installed${NC}"
		echo -e "${YELLOW}Please install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
		exit 1
	fi

	# Sync Python dependencies including dev dependencies
	if ! uv sync --dev; then
		echo -e "${RED}Error: Failed to sync Python environment${NC}"
		exit 1
	fi

	echo -e "${GREEN}✓ Python environment ready${NC}"
}

# Function to run lintro with enhanced error handling
run_lintro() {
	echo -e "${BLUE}Running lintro with arguments: $*${NC}"

	# Add some helpful defaults if no arguments provided
	if [ $# -eq 0 ]; then
		echo -e "${YELLOW}No arguments provided. Running 'lintro --help'${NC}"
		set -- "--help"
	fi

	# Run lintro with the provided arguments
	# In Docker, use uv run to execute within the container's virtual environment
	if [ -n "${RUNNING_IN_DOCKER:-}" ] || [ -f "/.dockerenv" ]; then
		uv run lintro "$@"
		local exit_code=$?
		if [ $exit_code -eq 0 ]; then
			echo -e "${GREEN}✓ Lintro completed successfully${NC}"
		else
			echo -e "${RED}✗ Lintro exited with code $exit_code${NC}"
		fi
		exit $exit_code
	else
		if uv run lintro "$@"; then
			echo -e "${GREEN}✓ Lintro completed successfully${NC}"
		else
			local exit_code=$?
			echo -e "${RED}✗ Lintro exited with code $exit_code${NC}"

			# Provide helpful suggestions based on common issues
			if [ $exit_code -eq 127 ]; then
				echo -e "${YELLOW}Tip: Command not found. Check if lintro is properly installed.${NC}"
			elif [ $exit_code -eq 1 ]; then
				echo -e "${YELLOW}Tip: Lintro found issues. Use 'fmt' command to auto-fix where possible.${NC}"
			fi

			exit $exit_code
		fi
	fi
}

# Main execution flow
main() {
	# Check and install tools if needed (only if --install flag is provided)
	if [ "${1:-}" = "--install" ] || [ "${1:-}" = "-i" ]; then
		check_and_install_tools
		shift # Remove the --install flag from arguments
	fi
	# Non-interactive acceptance
	if [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; then
		export ASSUME_YES=1
		shift
	fi

	# Setup Python environment
	setup_python_env

	# Run lintro
	run_lintro "$@"
}

# Show usage if --help is requested directly for this script
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	echo "Usage: $0 [--install|-i] [--yes|-y] [lintro arguments...]"
	echo ""
	echo "Options:"
	echo "  --install, -i    Install missing tools before running lintro"
	echo "  --yes, -y        Non-interactive auto-install when --install is used"
	echo ""
	echo "Examples:"
	echo "  $0 check"
	echo "  $0 --install --yes fmt --tools prettier"
	echo "  $0 list-tools"
	echo ""
	echo "This script automatically sets up the Python environment and runs lintro."
	echo "Use --install to automatically install missing external tools."
	exit 0
fi

# Run main function with all arguments
main "$@"
