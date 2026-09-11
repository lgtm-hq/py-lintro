#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Verify a built lintro binary before it is packaged: --version,
# every exported CLI command run once, the interactive AI review under a pty,
# an MCP JSON-RPC round trip over stdio, and --help.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 1 ]]; then
	cat <<'EOF'
Verify a built lintro binary before packaging.

Usage: verify_built_binary.sh <binary-path>

Runs --version (required), every exported CLI command once inside a
throwaway workspace (required), the interactive AI review through a pty
(required), one MCP initialize + tools/list round trip over stdio
(required) and --help (non-fatal truncation) checks.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

BINARY="$(cd "$(dirname "$1")" 2>/dev/null && pwd)/$(basename "$1")"

if [[ ! -f "$BINARY" ]]; then
	log_error "Binary not found: $1"
	exit 1
fi

# Display-only: a missing ls must not skip the --version gate.
ls -lh "$(dirname "$BINARY")" || true

# --version is the build gate: a binary that cannot report its version is not
# a valid build.
"$BINARY" --version

HELP_OUTPUT="$(mktemp)"
COMMAND_OUTPUT="$(mktemp)"
WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$HELP_OUTPUT" "$COMMAND_OUTPUT" "$WORKSPACE"' EXIT

# Every command the CLI exports, by canonical name (#2577). Pinned to
# lintro.cli's command table by tests/scripts/test_release_gate_contracts.py,
# so a command added to lintro without a case below fails the suite instead of
# shipping unexercised. --version and --help catch a binary that cannot start;
# only running each command catches one that dropped a module the command
# imports lazily, which is how `lintro mcp` shipped broken in every release
# before this gate existed.
EXPORTED_COMMANDS=(
	badge
	check
	completions
	config
	deps
	doctor
	format
	init
	install
	licenses
	list-tools
	mcp
	review
	test
	versions
	watch
)

# Whole-command budget. `timeout(1)` is GNU coreutils and absent from the
# macOS runners, so the wait is a poll loop; the override exists for the bats
# suite.
COMMAND_BUDGET_SECONDS="${LINTRO_VERIFY_COMMAND_BUDGET_SECONDS:-120}"

# What `lintro watch` prints once it is running; pinned against the command's
# own output in tests/scripts/test_release_gate_contracts.py.
WATCH_READY_MARKER="Watching for changes"

# Output that means the command crashed on a packaging defect rather than
# reporting a result: a missing module or an unhandled exception.
CRASH_MARKERS=("Traceback" "No module named")

# Throwaway workspace the commands run in: a git repository with one staged
# file for the review, a config that points the AI features at the fake
# provider fixture, and nothing else. `init` writes its config elsewhere so it
# does not clobber this one.
git -C "$WORKSPACE" init -q
git -C "$WORKSPACE" -c user.email=gate@lintro -c user.name=lintro-gate \
	commit -q --allow-empty -m "gate"
printf 'import os\nimport sys\n' >"$WORKSPACE/bad.py"
git -C "$WORKSPACE" add bad.py
cat >"$WORKSPACE/.lintro-config.yaml" <<'EOF'
ai:
  enabled: true
  review: true
  provider: anthropic
  transport: cli
EOF
mkdir -p "$WORKSPACE/init"

# The fake CLI providers the interactive review driver also uses: `review`
# talks to `claude` and `check`/`format` shell out to `ruff`.
export PATH="$SCRIPT_DIR/fixtures/fake-claude:$SCRIPT_DIR/fixtures/fake-ruff:$PATH"
# Hermetic: a developer's ~/.lintro-config.yaml must not reach these runs.
export LINTRO_GLOBAL_CONFIG=off

# Args:
#   $1: canonical command name.
# Prints the argv (after the binary) that runs the command once, to stdout,
# one argument per line.
command_argv() {
	case "$1" in
	badge) printf '%s\n' badge --errors 0 --warnings 0 --info 0 --url ;;
	check) printf '%s\n' check --output-format json --tools ruff . ;;
	completions) printf '%s\n' completions bash ;;
	config) printf '%s\n' config --json ;;
	deps) printf '%s\n' deps --format json ;;
	doctor) printf '%s\n' doctor --json ;;
	format) printf '%s\n' format --output-format json --tools ruff . ;;
	init) printf '%s\n' init --static --output "$WORKSPACE/init/.lintro-config.yaml" ;;
	install) printf '%s\n' install --dry-run --yes ruff ;;
	licenses) printf '%s\n' licenses --format json ;;
	list-tools) printf '%s\n' list-tools --json ;;
	review) printf '%s\n' review --uncommitted --output json ;;
	test) printf '%s\n' test --collect-only . ;;
	versions) printf '%s\n' versions --json ;;
	watch) printf '%s\n' watch --debounce 100 . ;;
	*)
		log_error "no invocation defined for command: $1"
		return 1
		;;
	esac
}

# Exit codes a command may report and still pass. 1 is "issues found" for the
# lint-style commands and "a tool is missing" for `test` (the binary bundles
# no pytest); anything else is a crash or a usage error.
accepted_exit_codes() {
	case "$1" in
	check | format | test | doctor) printf '0 1' ;;
	*) printf '0' ;;
	esac
}

# Args:
#   $1: label for log lines.
#   $2: file holding the command's combined output.
# Fails when the output carries a crash marker.
assert_no_crash() {
	local marker
	for marker in "${CRASH_MARKERS[@]}"; do
		if grep -q "$marker" "$2"; then
			log_error "$1 crashed (output contains '$marker'):"
			cat "$2"
			return 1
		fi
	done
}

# Args:
#   $1: pid to wait for.
#   $2: budget in seconds.
# Sets WAIT_STATUS to the exit status, or to the empty string when the
# process outlived the budget (it is then killed). A variable, not stdout:
# `wait` only reaps children of the shell that started them, which a command
# substitution's subshell did not.
WAIT_STATUS=""
wait_within_budget() {
	local pid="$1" budget="$2"
	WAIT_STATUS=""
	for _ in $(seq 1 "$budget"); do
		if ! kill -0 "$pid" 2>/dev/null; then
			if wait "$pid"; then
				WAIT_STATUS=0
			else
				WAIT_STATUS=$?
			fi
			return 0
		fi
		sleep 1
	done
	kill -9 "$pid" 2>/dev/null || true
	wait "$pid" 2>/dev/null || true
}

# Args:
#   $1: canonical command name.
# Runs the command once in the workspace and asserts it exited with an
# accepted code without crashing.
run_command_once() {
	local name="$1" status accepted
	local -a argv
	argv=()
	while IFS= read -r arg; do
		argv+=("$arg")
	done < <(command_argv "$name")

	(cd "$WORKSPACE" && exec "$BINARY" "${argv[@]}" </dev/null >"$COMMAND_OUTPUT" 2>&1) &
	wait_within_budget $! "$COMMAND_BUDGET_SECONDS"
	status="$WAIT_STATUS"
	if [[ -z "$status" ]]; then
		log_error "$name did not exit within ${COMMAND_BUDGET_SECONDS}s:"
		cat "$COMMAND_OUTPUT"
		return 1
	fi

	assert_no_crash "$name" "$COMMAND_OUTPUT"
	accepted="$(accepted_exit_codes "$name")"
	if [[ " $accepted " != *" $status "* ]]; then
		log_error "$name exited $status (accepted: $accepted):"
		cat "$COMMAND_OUTPUT"
		return 1
	fi
	log_success "$name: OK (exit $status)"
}

# `watch` never exits on its own: start it, wait for the ready line, and stop
# it. A binary that dropped the file-watching stack dies before the line.
run_watch_once() {
	local pid elapsed=0
	local -a argv
	argv=()
	while IFS= read -r arg; do
		argv+=("$arg")
	done < <(command_argv watch)

	(cd "$WORKSPACE" && exec "$BINARY" "${argv[@]}" </dev/null >"$COMMAND_OUTPUT" 2>&1) &
	pid=$!
	while ! grep -q "$WATCH_READY_MARKER" "$COMMAND_OUTPUT"; do
		if ! kill -0 "$pid" 2>/dev/null; then
			wait "$pid" 2>/dev/null || true
			log_error "watch exited before it was watching:"
			cat "$COMMAND_OUTPUT"
			return 1
		fi
		if ((elapsed >= COMMAND_BUDGET_SECONDS)); then
			kill -9 "$pid" 2>/dev/null || true
			wait "$pid" 2>/dev/null || true
			log_error "watch was not ready within ${COMMAND_BUDGET_SECONDS}s:"
			cat "$COMMAND_OUTPUT"
			return 1
		fi
		sleep 1
		elapsed=$((elapsed + 1))
	done
	kill "$pid" 2>/dev/null || true
	wait "$pid" 2>/dev/null || true
	assert_no_crash watch "$COMMAND_OUTPUT"
	log_success "watch: OK (ready, then stopped)"
}

# `mcp` speaks JSON-RPC over stdio, so the driver completes one initialize +
# tools/list round trip and requires a clean exit at EOF. No escape hatch: a
# build that silently dropped the SDK used to be accepted on its usage error,
# and that is how #2577 shipped.
run_mcp_once() {
	python3 "$SCRIPT_DIR/drive_mcp_round_trip.py" "$BINARY"
}

for name in "${EXPORTED_COMMANDS[@]}"; do
	case "$name" in
	mcp) run_mcp_once ;;
	watch) run_watch_once ;;
	*) run_command_once "$name" ;;
	esac
done

# #2514: lintro and pygments ship as bytecode rather than compiled C, and the
# pygments lexers are resolved by name at runtime, so a packaging mistake there
# is invisible to --version, --help and the tool-registry smoke test. The
# driver reviews an AI fix inside the built binary under a pty and fails unless
# the diff came back highlighted.
python3 "$SCRIPT_DIR/drive_interactive_review.py" "$BINARY"

# --help stays non-fatal (it is diagnostic output only), but capture it before
# truncating: piping straight into `head` under `set -o pipefail` turns head's
# SIGPIPE into a failure that is indistinguishable from a genuinely broken
# --help, and the old `|| echo "Help output truncated"` swallowed both.
if "$BINARY" --help >"$HELP_OUTPUT" 2>&1; then
	head -20 "$HELP_OUTPUT"
else
	log_warning "--help exited non-zero (non-fatal); first 20 lines follow"
	head -20 "$HELP_OUTPUT"
fi
