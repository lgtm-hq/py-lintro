#!/usr/bin/env bash
set -euo pipefail

# docker-lintro.sh - Run lintro in a Docker container
#
# This script allows running lintro without installing all the dependencies locally.
# It uses the Docker entrypoint directly for consistent execution across all workflows.
#
# Usage:
#   ./docker-lintro.sh check --tools hadolint,prettier [PATH]
#   ./docker-lintro.sh format --tools ruff [PATH]
#   ./docker-lintro.sh list-tools

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../utils/utils.sh"

# Show help if requested
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: docker-lintro.sh [--help] [lintro arguments...]

Docker Lintro Runner
Run Lintro in a Docker container without installing dependencies locally.

Features:
  - Builds Docker image if not exists
  - Mounts current directory to /code in container
  - Uses Docker entrypoint directly for consistent execution

Examples:
  docker-lintro.sh check
  docker-lintro.sh check --tools ruff,prettier
  docker-lintro.sh format --tools ruff
  docker-lintro.sh list-tools

This script allows running lintro without installing all dependencies locally.
EOF
	exit 0
fi

echo -e "${BLUE}=== Docker Lintro Runner ===${NC}"

# Check if Docker is installed
if ! command -v docker &>/dev/null; then
	echo -e "${RED}Error: Docker is not installed or not in PATH${NC}"
	exit 1
fi

# Build the Docker image if it doesn't exist
IMAGE_NAME="py-lintro:latest"
if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
	echo -e "${YELLOW}Building Docker image...${NC}"
	if ! docker build --target full -t "$IMAGE_NAME" .; then
		echo -e "${RED}Error: Failed to build Docker image${NC}"
		exit 1
	fi
	echo -e "${GREEN}✓ Docker image built successfully${NC}"
else
	echo -e "${GREEN}✓ Using existing Docker image${NC}"
fi

# Run lintro in Docker using the Docker entrypoint directly
# We mount the current directory to /code to match ci-lintro.sh
# Note: pydoclint timeout increased for Docker (Docker is slower than local)
echo -e "${BLUE}Running lintro in Docker container...${NC}"
echo -e "${YELLOW}Arguments: $*${NC}"

# Check if the command is 'check' and add pydoclint timeout if not already specified
if [[ "$1" == "check" ]] && [[ "$*" != *"--tool-options"*"pydoclint"* ]]; then
	docker run --rm \
		--log-driver=local \
		-v "$(pwd):/code" \
		-w /code \
		"$IMAGE_NAME" \
		lintro "$@" --tool-options pydoclint:timeout=120
else
	docker run --rm \
		--log-driver=local \
		-v "$(pwd):/code" \
		-w /code \
		"$IMAGE_NAME" \
		lintro "$@"
fi

EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
	echo -e "${GREEN}✓ Docker lintro completed${NC}"
else
	echo -e "${RED}✗ Docker lintro failed with exit code $EXIT_CODE${NC}"
fi
exit $EXIT_CODE
