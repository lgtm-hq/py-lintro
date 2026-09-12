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
# model defaults to the one already recorded for that CLI, except for codex,
# which is sent no model at all unless one is passed here (#2537: a ChatGPT
# plan serves only its own catalogue, so any pinned slug fails outright).
# Recording spends quota: it makes one real call.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE_ROOT="${REPO_ROOT}/tests/fixtures/ai/cli_replay"
PROMPT="Reply with the single word: pong"

# Print the header block above verbatim. Anchored on its first and last
# comment lines rather than a line range, so editing the header cannot
# silently mis-slice --help.
usage() {
	sed -n '/^# Re-record an agent-CLI replay fixture/,/^# Recording spends quota: it makes one real call/p' \
		"${BASH_SOURCE[0]}"
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
	# Deliberately empty (#2537): lintro's production codex path sends no
	# --model when none is configured, because a ChatGPT-plan session serves
	# only the models that plan offers and an API-catalogue slug fails the
	# call outright. Pass a model as $3 to override.
	default_model=""
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

echo "recording $cli $version (${model:-CLI default}) -> $recording"

# Record into a temporary file and move it into place only once the CLI has
# exited successfully. Redirecting straight at the fixture would truncate a
# good recording the moment the call fails on auth, quota or transport.
scratch="$(mktemp "${target_dir}/.${version}.jsonl.XXXXXX")"
cleanup() { rm -f "$scratch"; }
trap cleanup EXIT

case "$cli" in
claude)
	"$binary" --print --output-format json --model "$model" "$PROMPT" >"$scratch"
	;;
codex)
	# No --model unless one was asked for; see default_model above.
	if [[ -n "$model" ]]; then
		"$binary" exec --json --model "$model" "$PROMPT" >"$scratch"
	else
		"$binary" exec --json "$PROMPT" >"$scratch"
	fi
	;;
cursor)
	"$binary" --print --output-format json --model "$model" "$PROMPT" >"$scratch"
	;;
esac

mv "$scratch" "$recording"

# The expectation records the model the transport is constructed with. When no
# model was sent (codex), the CLI does not report the one it chose, so the
# label stays whatever the committed expectation already carried; pass a model
# as $3 to set it.
expectation="${recording%.jsonl}.expected.json"
expected_model="$model"
if [[ -z "$expected_model" ]]; then
	if [[ ! -f "$expectation" ]]; then
		echo "no committed expectation for $cli $version; pass the model as \$3" >&2
		exit 2
	fi
	expected_model="$(
		EXPECTATION="$expectation" python3 -c \
			'import json, os; print(json.load(open(os.environ["EXPECTATION"]))["model"])'
	)"
fi

# The recording is the evidence; the expectation is what lintro's own parser
# makes of it. Both are committed, so the next drift shows up as a diff.
(cd "$REPO_ROOT" && uv run python -m tests.unit.ai.providers.cli_replay \
	--cli "$cli" \
	--recording "$recording" \
	--model "$expected_model")

echo "Review both files before committing; they must contain no credentials."
