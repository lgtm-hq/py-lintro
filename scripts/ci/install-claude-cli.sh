#!/usr/bin/env bash
set -euo pipefail

# install-claude-cli.sh - install the pinned Anthropic `claude` CLI on a runner
#
# The AI review dogfood runs `lintro review --transport cli`, which shells out to
# the `claude` binary and authenticates through its OAuth session
# (CLAUDE_CODE_OAUTH_TOKEN). The binary therefore has to exist on the runner.
#
# The version is not chosen here: the caller resolves it from the ai-tools
# Dockerfile's Renovate-managed ARG (scripts/ci/ai_tools_arg_pin.py), so the CLI
# the dogfood drives is the same one the released `ai` image ships and the same
# one the contract tests verify. An unpinned `@latest` would install an
# unreviewed binary into a job that holds a credential (#1611 is what an
# unnoticed CLI bump costs).
#
# The install is verified with `claude --version`, which is also the probe
# lintro's version floor and capability gate use — so a binary that cannot even
# answer that fails here rather than halfway through a review.
#
# Usage:
#   CLAUDE_CODE_VERSION=2.1.220 scripts/ci/install-claude-cli.sh
#
# Environment:
#   CLAUDE_CODE_VERSION  npm @anthropic-ai/claude-code version (required)

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: CLAUDE_CODE_VERSION=<version> scripts/ci/install-claude-cli.sh

Install the pinned @anthropic-ai/claude-code CLI globally and verify it runs.

Environment:
  CLAUDE_CODE_VERSION  npm version to install (required, exact — no ranges)
EOF
	exit 0
fi

if [[ -z "${CLAUDE_CODE_VERSION:-}" ]]; then
	echo "ERROR: CLAUDE_CODE_VERSION is required" >&2
	exit 1
fi

# Exact versions only. `latest`, `^2.1.220`, and npm aliases are all non-empty,
# so an emptiness check alone would let the installed binary move between runs
# — in a job that holds a credential, and behind a contract gate that verified a
# different version.
if [[ ! "${CLAUDE_CODE_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	echo "ERROR: CLAUDE_CODE_VERSION must be an exact X.Y.Z version" >&2
	exit 1
fi

echo "Installing @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}..."
npm install -g --no-fund --no-audit \
	"@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"

echo "Verifying the installed CLI answers --version..."
claude --version
