#!/usr/bin/env bash
set -euo pipefail

# docker-test.sh - Run tests in a Docker container
#
# This script runs the full test suite in a containerized environment
# where all tools are pre-installed. It delegates to run-tests.sh inside the container.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../utils/utils.sh"

# Show help if requested
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: docker-test.sh [--help]

Docker Integration Test Runner
Run integration tests in Docker container with all tools pre-installed.

Features:
  - Uses Docker Compose for test environment
  - All tools pre-installed in container
  - Delegates to run-tests.sh inside container
  - Provides clear success/failure output

This script runs the full test suite in a containerized environment.
EOF
	exit 0
fi

echo -e "${BLUE}=== Docker Integration Test Runner ===${NC}"

# Check if Docker is running
if ! docker info &>/dev/null; then
	echo -e "${RED}Error: Docker is not running. Please start Docker and try again.${NC}"
	exit 1
fi

# Use Docker Compose v2 (standard)
DOCKER_COMPOSE_CMD="docker compose"
echo -e "${GREEN}Using Docker Compose v2${NC}"

IMAGE_NAME="py-lintro-test:latest"

# Check if we need to build the Docker image
if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
	echo -e "${YELLOW}Building Docker image...${NC}"
	$DOCKER_COMPOSE_CMD build test-integration
	echo -e "${GREEN}✓ Docker image built successfully${NC}"
else
	echo -e "${GREEN}✓ Using existing Docker image${NC}"
fi

# Run the integration tests in Docker using run-tests.sh
echo -e "${BLUE}Running integration tests in Docker...${NC}"
echo -e "${YELLOW}All tools are pre-installed in the Docker environment${NC}"

if $DOCKER_COMPOSE_CMD run --rm test-integration; then
	echo -e "${GREEN}=== All tests passed! ===${NC}"
	exit 0
else
	echo -e "${RED}=== Tests failed! ===${NC}"
	exit 1
fi
