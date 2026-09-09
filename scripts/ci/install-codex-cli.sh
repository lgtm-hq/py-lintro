#!/usr/bin/env bash
set -euo pipefail

# install-codex-cli.sh - install the pinned OpenAI `codex` CLI on a runner
#
# The AI review dogfood can run `lintro review --transport cli` against the
# `openai` provider (#2472), which shells out to the `codex` binary and
# authenticates through its ChatGPT-plan session (~/.codex/auth.json). The
# binary therefore has to exist on the runner.
#
# The version is not chosen here: the caller resolves it from the ai-tools
# Dockerfile's Renovate-managed ARG (scripts/ci/ai_tools_arg_pin.py), so the
# CLI the dogfood drives is the same one the released `ai` image ships and the
# same one the contract tests verify. An unpinned `@latest` would install an
# unreviewed binary into a job that holds a credential.
#
# The install is verified with `codex --version`, which is also the probe
# lintro's version floor and capability gate use — so a binary that cannot
# even answer that fails here rather than halfway through a review.
#
# Usage:
#   CODEX_VERSION=0.147.0 scripts/ci/install-codex-cli.sh
#
# Environment:
#   CODEX_VERSION  npm @openai/codex version (required)

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: CODEX_VERSION=<version> scripts/ci/install-codex-cli.sh

Install the pinned @openai/codex CLI globally and verify it runs.

Environment:
  CODEX_VERSION  npm version to install (required, exact — no ranges)
EOF
	exit 0
fi

if [[ -z "${CODEX_VERSION:-}" ]]; then
	echo "ERROR: CODEX_VERSION is required" >&2
	exit 1
fi

# Exact versions only, mirroring install-claude-cli.sh: `latest`, `^0.147.0`,
# and npm aliases are all non-empty, so an emptiness check alone would let the
# installed binary move between runs — in a job that holds a credential, and
# behind a contract gate that verified a different version.
if [[ ! "${CODEX_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	echo "ERROR: CODEX_VERSION must be an exact X.Y.Z version" >&2
	exit 1
fi

echo "Installing @openai/codex@${CODEX_VERSION}..."
npm install -g --no-fund --no-audit "@openai/codex@${CODEX_VERSION}"

echo "Verifying the installed CLI answers --version..."
codex --version
