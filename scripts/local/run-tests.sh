#!/usr/bin/env bash
set -euo pipefail

# run-tests.sh - Universal test runner (works locally and in Docker)
#
# This script handles the complete setup and execution of tests locally.
# It automatically checks tool availability and runs appropriate tests.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../utils/utils.sh"

# Global variables
VERBOSE=0
TEST_FILES=()

# Function to check if venv is valid and functional
check_venv_valid() {
	local venv_python="$1"

	# Check 1: Binary exists and is executable
	if [ ! -x "$venv_python" ]; then
		echo "missing"
		return 1
	fi

	# Check 2: Python can actually execute (not broken symlink or incompatible binary)
	if ! "$venv_python" -c "import sys" 2>/dev/null; then
		echo "broken"
		return 1
	fi

	# Check 3: Key packages are importable (pytest is essential for tests)
	if ! "$venv_python" -c "import pytest" 2>/dev/null; then
		echo "incomplete"
		return 1
	fi

	# Check 4: lintro module is importable
	if ! "$venv_python" -c "import lintro" 2>/dev/null; then
		echo "stale"
		return 1
	fi

	echo "valid"
	return 0
}

# Function to setup Python environment
setup_python_env() {
	echo -e "${BLUE}Setting up Python environment...${NC}"

	# Check if we're running in Docker
	if [ -n "$RUNNING_IN_DOCKER" ]; then
		echo -e "${YELLOW}Running in Docker environment${NC}"
		# The host bind mount can mask the image venv; rebuild it if missing or invalid.
		cd /app
		local venv_status
		venv_status=$(check_venv_valid "/app/.venv/bin/python" || true)
		if [ "$venv_status" != "valid" ]; then
			echo -e "${YELLOW}Detected ${venv_status} /app/.venv; rebuilding venv with uv...${NC}"
			# Remove potentially corrupted venv to ensure clean rebuild
			rm -rf /app/.venv 2>/dev/null || true
			export UV_LINK_MODE=${UV_LINK_MODE:-copy}
			uv sync --dev --extra tools --no-progress
		fi
	else
		echo -e "${YELLOW}Running in local environment${NC}"
		# In local environment, ensure we have uv and dependencies
		if ! command -v uv &>/dev/null; then
			echo -e "${RED}Error: uv is not installed. Please install uv first.${NC}"
			exit 1
		fi

		# Sync dependencies
		uv sync --dev --no-progress
	fi

	echo -e "${GREEN}✓ Python environment ready${NC}"
}

# Function to check if a tool is available (for Python packages)
check_tool_availability() {
	local tool_name="$1"
	local check_cmd="$2"

	echo -e "${BLUE}Checking for $tool_name...${NC}"
	if $check_cmd &>/dev/null; then
		echo -e "${GREEN}✓ $tool_name found and working - including $tool_name tests${NC}"
		return 0
	else
		echo -e "${YELLOW}✗ $tool_name not found or not working - skipping $tool_name tests${NC}"
		return 1
	fi
}

# Function to check if a system tool is available
check_system_tool() {
	local tool_name="$1"
	local check_cmd="$2"

	echo -e "${BLUE}Checking for $tool_name...${NC}"
	if $check_cmd &>/dev/null; then
		echo -e "${GREEN}✓ $tool_name found and working - including $tool_name tests${NC}"
		return 0
	else
		echo -e "${YELLOW}✗ $tool_name not found or not working - skipping $tool_name tests${NC}"
		return 1
	fi
}

# Function to discover available tools and build test list
discover_tests() {
	echo -e "${BLUE}Discovering tests to run...${NC}"
	# Always run all tests in the tests directory
	# SC2034: TEST_FILES is exported for use in subshells and other scripts
	# shellcheck disable=SC2034
	TEST_FILES=("tests")
	echo -e "${GREEN}All tests in the tests directory will be run.${NC}"
}

# Ensure required Python CLI tools are available in the active uv environment
ensure_python_cli_tools() {
	echo -e "${BLUE}Ensuring required Python CLI tools are available...${NC}"
	# Ensure core Python CLIs needed for integration tests
	local pkgs=(
		"bandit==1.8.6"
		"black"
		"mypy>=1.14.1"
		"ruff>=0.14.0"
		"yamllint>=1.37.1"
		"pydoclint>=0.5.0"
		"pytest>=9.0.1"
	)
	for pkg in "${pkgs[@]}"; do
		# Derive import name from package (strip version spec)
		local import_name="${pkg%%[=> ]*}"
		if ! uv run python -c "import ${import_name}" >/dev/null 2>&1; then
			echo -e "${YELLOW}Installing ${pkg} for integration tests...${NC}"
			uv pip install "${pkg}" >/dev/null 2>&1 || true
		fi
	done
}

# Helper function to run tests and report results
run_and_report_tests() {
	local test_cmd=("$@")

	if "${test_cmd[@]}"; then
		echo -e "${GREEN}✓ Tests completed successfully${NC}"
		echo ""
		echo -e "${GREEN}Coverage reports generated:${NC}"
		echo -e "  ${BLUE}- Terminal: displayed above${NC}"
		echo -e "  ${BLUE}- HTML: htmlcov/index.html${NC}"
		echo -e "  ${BLUE}- XML: coverage.xml${NC}"

		# Update coverage badge when running on host (not container-only overlays)
		if [ -f "coverage.xml" ]; then
			echo -e "${BLUE}Updating coverage badge...${NC}"
			if ./scripts/ci/coverage-badge-update.sh >/dev/null 2>&1; then
				echo -e "${GREEN}✓ Coverage badge updated${NC}"
			else
				echo -e "${YELLOW}⚠ Could not update coverage badge${NC}"
			fi
		fi

		if [ -f "htmlcov/index.html" ]; then
			echo ""
			echo -e "${YELLOW}To view detailed coverage report:${NC}"
			echo -e "  open htmlcov/index.html"
		fi
		return 0
	else
		echo -e "${RED}✗ Tests failed${NC}"
		return 1
	fi
}

# Function to run tests with coverage
run_tests() {
	echo -e "${BLUE}Running all tests in the tests directory...${NC}"

	# Clean up any existing coverage files to prevent corruption with parallel execution
	echo -e "${YELLOW}Cleaning up existing coverage files...${NC}"
	find . -type f -name ".coverage*" -not -path "./.venv/*" -not -path "./.git/*" -delete 2>/dev/null || true

	# Avoid uv hardlink warnings/noise by defaulting to copy mode
	export UV_LINK_MODE=${UV_LINK_MODE:-copy}

	# Determine pytest worker count.
	# Default to "auto" (uses os.cpu_count(), respects cgroup CPU limits in Docker).
	# Override with LINTRO_PYTEST_WORKERS=0 to force serial execution.
	# Historical note: Docker parallel was previously disabled due to execnet /dev/shm
	# exhaustion (fixed via shm_size in docker-compose.yml) and Docker build contention
	# (mitigated by DOCKER_BUILDKIT=0 in conftest.py and docker-compose.yml).
	local workers="${LINTRO_PYTEST_WORKERS:-auto}"

	# Build lintro tst arguments
	local tst_args=("tests")

	# Add verbose flag if requested
	local tool_opts="pytest:coverage_report=True,pytest:coverage_html=htmlcov,pytest:coverage_xml=coverage.xml,pytest:timeout=600"
	if [ "$VERBOSE" = "1" ] || [ "${1:-}" = "--verbose" ] || [ "${1:-}" = "-v" ]; then
		echo -e "${YELLOW}Running tests in verbose mode${NC}"
		tst_args+=("--verbose")
		tool_opts="${tool_opts},pytest:verbose=True"
	fi

	# Always pass workers to ensure explicit control via LINTRO_PYTEST_WORKERS
	tool_opts="${tool_opts},pytest:workers=${workers}"

	# Add pytest-sugar for enhanced CI output (if available)
	if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
		if python -c "import pytest_sugar" 2>/dev/null; then
			echo -e "${YELLOW}Using pytest-sugar for enhanced CI output${NC}"
			tool_opts="${tool_opts},pytest:show_progress=False"
		fi
	fi

	tst_args+=("--tool-options" "${tool_opts}")

	# Determine which command to use based on environment
	if [ -n "${RUNNING_IN_DOCKER:-}" ]; then
		echo -e "${YELLOW}Using lintro tst in Docker environment${NC}"
		local cmd_prefix="/app/.venv/bin/python -m lintro tst"
	else
		echo -e "${YELLOW}Using lintro tst via uv${NC}"
		local cmd_prefix="uv run lintro tst"
	fi

	echo -e "${BLUE}Executing: ${cmd_prefix} ${tst_args[*]}${NC}"
	# SC2086: cmd_prefix contains multiple space-separated args
	# shellcheck disable=SC2086
	run_and_report_tests ${cmd_prefix} "${tst_args[@]}"
	return $?
}

# Function to provide helpful tips
show_tips() {
	echo ""
	echo -e "${YELLOW}=== Helpful Tips ===${NC}"
	echo -e "${BLUE}• Install missing tools: ./scripts/local/local-lintro.sh --install${NC}"
	echo -e "${BLUE}• Run specific tests: uv run lintro tst tests/ --tool-options pytest:workers=auto${NC}"
	echo -e "${BLUE}• Run with verbose output: $0 --verbose${NC}"
	echo -e "${BLUE}• Check tool installation: ./scripts/utils/install-tools.sh --local${NC}"
	echo ""
}

# Main execution flow
main() {
	local exit_code=0

	echo -e "${BLUE}=== Lintro Local Test Runner ===${NC}"

	# Load environment variables from .env file if it exists
	if [ -f .env ]; then
		echo -e "${YELLOW}Loading environment variables from .env file...${NC}"
		# SC2046: word splitting is intentional for env var export
		# shellcheck disable=SC2046
		export $(grep -v '^#' .env | xargs)
	fi

	# Handle command line arguments
	# SC2034: verbose is used for conditional logic and exported via VERBOSE
	# shellcheck disable=SC2034
	local verbose=false
	if [ "${1:-}" = "--verbose" ] || [ "${1:-}" = "-v" ]; then
		# shellcheck disable=SC2034
		verbose=true
		VERBOSE=1
		echo -e "${YELLOW}Verbose mode enabled${NC}"
	fi

	# Setup Python environment
	setup_python_env
	# Ensure Python CLI tools used by integration tests are present
	ensure_python_cli_tools

	# Install project-level node_modules for integration tests that need
	# npm plugins (e.g. prettier-plugin-astro for astro formatting tests)
	if command -v bun &>/dev/null && [ -f "package.json" ] && [ ! -d "node_modules" ]; then
		echo -e "${BLUE}Installing project node_modules for integration tests...${NC}"
		if ! bun install --no-save; then
			echo -e "${RED}✗ bun install failed — astro/prettier integration tests may be skipped${NC}"
			exit 1
		fi
	fi

	# Discover available tools and tests
	discover_tests

	# Run the tests
	if run_tests "$@"; then
		echo -e "${GREEN}=== All tests passed! ===${NC}"
		exit_code=0

		# Copy coverage files to /code if we're in Docker and /code exists

		# Small delay to ensure files are fully written
		if [ -n "${COVERAGE_OUTPUT_DIR:-}" ]; then
			dest_dir="${COVERAGE_OUTPUT_DIR}"
		elif [ -d "/app" ] && [ -w "/app" ]; then
			dest_dir="/app"
		else
			dest_dir=""
		fi

		if [ -n "$dest_dir" ] && [ -d "$dest_dir" ]; then
			echo -e "${YELLOW}Waiting for files to be fully written...${NC}"
			sleep 1
			# If destination is the current directory, skip redundant copy
			if [ "$(pwd)" = "$dest_dir" ]; then
				echo -e "${YELLOW}Destination equals working directory; skipping copy${NC}"
				return 0
			fi
			echo -e "${BLUE}Copying coverage files to ${dest_dir}...${NC}"

			# Fix permissions on destination if running as root
			if [ "$(whoami)" = "root" ]; then
				echo -e "${YELLOW}Fixing permissions on ${dest_dir}...${NC}"
				chown -R root:root "$dest_dir" 2>/dev/null || true
				chmod -R 755 "$dest_dir" 2>/dev/null || true
			fi

			# Copy htmlcov directory
			echo -e "${YELLOW}Copying htmlcov to ${dest_dir}...${NC}"
			if [ -d "htmlcov" ]; then
				cp -rv htmlcov/ "$dest_dir/" 2>&1 && echo -e "${GREEN}✓ htmlcov copied successfully${NC}" || echo -e "${RED}✗ Could not copy htmlcov${NC}"
			else
				echo -e "${RED}✗ htmlcov directory not found${NC}"
			fi

			# Copy coverage.xml file
			echo -e "${YELLOW}Copying coverage.xml to ${dest_dir}...${NC}"
			if [ -f "coverage.xml" ]; then
				cp -v coverage.xml "$dest_dir/" 2>&1 && echo -e "${GREEN}✓ coverage.xml copied successfully${NC}" || echo -e "${RED}✗ Could not copy coverage.xml${NC}"
			else
				echo -e "${RED}✗ coverage.xml file not found${NC}"
			fi

			# Copy .coverage file
			echo -e "${YELLOW}Copying .coverage to ${dest_dir}...${NC}"
			if [ -f ".coverage" ]; then
				cp -v .coverage "$dest_dir/" 2>&1 && echo -e "${GREEN}✓ .coverage copied successfully${NC}" || echo -e "${RED}✗ Could not copy .coverage${NC}"
			else
				echo -e "${RED}✗ .coverage file not found${NC}"
			fi

			# Verify files were copied
			echo -e "${YELLOW}Verifying files in ${dest_dir} after copy:${NC}"
			if [ -f "${dest_dir}/coverage.xml" ]; then
				echo -e "${GREEN}✓ ${dest_dir}/coverage.xml exists (size: $(wc -c <"${dest_dir}/coverage.xml") bytes)${NC}"
			else
				echo -e "${RED}✗ ${dest_dir}/coverage.xml not found${NC}"
			fi

			if [ -d "${dest_dir}/htmlcov" ]; then
				echo -e "${GREEN}✓ ${dest_dir}/htmlcov directory exists${NC}"
			else
				echo -e "${RED}✗ ${dest_dir}/htmlcov directory not found${NC}"
			fi

			echo -e "${GREEN}✓ Coverage files copy process completed${NC}"
		fi
	else
		echo -e "${RED}=== Tests failed! ===${NC}"
		exit_code=1
	fi

	# Show helpful tips
	show_tips

	exit $exit_code
}

# Show usage information
show_usage() {
	echo "Usage: $0 [--verbose|-v]"
	echo ""
	echo "This script automatically:"
	echo "  1. Sets up the Python environment"
	echo "  2. Discovers available linting tools"
	echo "  3. Runs all core tests plus integration tests for available tools"
	echo "  4. Generates coverage reports"
	echo ""
	echo "Options:"
	echo "  --verbose, -v    Run tests with verbose output"
	echo ""
	echo "The script will run all core tests and skip integration tests for tools that aren't installed."
	echo "Use './scripts/local/local-lintro.sh --install' to install missing tools."
}

# Handle help request or docker delegate
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	show_usage
	echo ""
	echo "Options:"
	echo "  --docker        Run tests inside Docker via scripts/docker/docker-test.sh"
	exit 0
fi

# Delegate to Docker-based test runner when requested
if [ "${1:-}" = "--docker" ]; then
	SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
	DOCKER_SCRIPT="${SCRIPT_DIR%/local}/docker/docker-test.sh"
	if [ ! -x "$DOCKER_SCRIPT" ]; then
		echo -e "${RED}Error: Docker test script not found at $DOCKER_SCRIPT${NC}"
		exit 1
	fi
	exec "$DOCKER_SCRIPT"
fi

# Run main function
main "$@"
