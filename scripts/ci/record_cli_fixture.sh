#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Re-record an agent-CLI replay fixture (#2600).
#
# The fixtures under tests/fixtures/ai/cli_replay hold what each supported CLI
# actually printed for a trivial prompt at the pinned version. The Tier 1
# replay test parses them with the real transport parsers on every PR, so
# vendor schema drift is a diff rather than a broken review — but only while
# the recordings describe the binaries the ai-tools image ships. Re-record when
# a pin in docker/ai-tools.Dockerfile moves.
#
# Usage:
#   scripts/ci/record_cli_fixture.sh <claude|codex|cursor> [version] [model]
#
# The version defaults to what the binary reports and must match the pin; the
# model defaults to the one already recorded for that CLI. Recording spends
# quota: it makes one real call.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE_ROOT="${REPO_ROOT}/tests/fixtures/ai/cli_replay"
PROMPT="Reply with the single word: pong"

usage() {
	sed -n '5,19p' "${BASH_SOURCE[0]}"
}

cli="${1:-}"
if [[ -z "$cli" || "$cli" == "--help" || "$cli" == "-h" ]]; then
	usage
	[[ -n "$cli" ]] && exit 0
	exit 2
fi

case "$cli" in
claude)
	binary="claude"
	default_model="claude-sonnet-4-6"
	;;
codex)
	binary="codex"
	default_model="gpt-5.1-codex"
	;;
cursor)
	binary="agent"
	default_model="auto"
	;;
*)
	echo "unknown CLI '$cli' (expected claude, codex or cursor)" >&2
	exit 2
	;;
esac

if ! command -v "$binary" >/dev/null 2>&1; then
	echo "$binary is not on PATH; run inside the lintro-ai-tools image" >&2
	exit 1
fi

version="${2:-$("$binary" --version | tr -d '\n' | grep -oE '[0-9][^ ]*' | head -n1)}"
model="${3:-$default_model}"
target_dir="${FIXTURE_ROOT}/${cli}"
recording="${target_dir}/${version}.jsonl"
mkdir -p "$target_dir"

echo "recording $cli $version ($model) -> $recording"
case "$cli" in
claude)
	"$binary" --print --output-format json --model "$model" "$PROMPT" >"$recording"
	;;
codex)
	"$binary" exec --json --model "$model" "$PROMPT" >"$recording"
	;;
cursor)
	"$binary" --print --output-format json --model "$model" "$PROMPT" >"$recording"
	;;
esac

# The recording is the evidence; the expectation is what lintro's own parser
# makes of it. Both are committed, so the next drift shows up as a diff.
(cd "$REPO_ROOT" && uv run python -m tests.unit.ai.providers.cli_replay \
	--cli "$cli" \
	--recording "$recording" \
	--model "$model")

echo "Review both files before committing; they must contain no credentials."
