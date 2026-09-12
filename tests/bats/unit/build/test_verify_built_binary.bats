#!/usr/bin/env bats
# SPDX-License-Identifier: MIT
# Purpose: Tests for scripts/build/verify_built_binary.sh

load "../../helpers/common"

SCRIPT="${BUILD_SCRIPTS_DIR}/verify_built_binary.sh"

setup() {
	setup_temp_dir
	WORKDIR="${BATS_TEST_TMPDIR}/work"
	mkdir -p "${WORKDIR}/dist/nuitka"
	BINARY="${WORKDIR}/dist/nuitka/lintro"
	# The verify step drives the pty review and the MCP round trip through
	# `python3`; stub both so the cases below exercise the shell branches
	# against a stub binary (#2514, #2577).
	STUB_BIN="${BATS_TEST_TMPDIR}/stub-bin"
	mkdir -p "$STUB_BIN"
	stub_drivers 0 "OK interactive review: pygments-highlighted diff" \
		0 "OK mcp round trip: lintro 0.0.0 listed 7 tools"
	PATH="${STUB_BIN}:${PATH}"
	export PATH
	# Every command runs inside a throwaway workspace; keep the poll loops
	# short so a stub that misbehaves fails fast.
	export LINTRO_VERIFY_COMMAND_BUDGET_SECONDS=5
}

# Write a fake `python3` standing in for both drivers.
#
# Args:
#   $1: exit status the review driver should report.
#   $2: line the review driver should print.
#   $3: exit status the MCP driver should report.
#   $4: line the MCP driver should print.
stub_drivers() {
	cat >"${STUB_BIN}/python3" <<EOF
#!/usr/bin/env bash
case "\${1:-}" in
*drive_interactive_review.py) echo "$2"; exit $1 ;;
*drive_mcp_round_trip.py) echo "$4"; exit $3 ;;
*) exit 0 ;;
esac
EOF
	chmod +x "${STUB_BIN}/python3"
}

# Write a stub binary that answers every exported command the way a healthy
# release binary does, with one command's behaviour overridable.
#
# Args:
#   $1: command name whose case arm is replaced (optional).
#   $2: shell fragment for that arm (optional).
write_healthy_binary() {
	local override_name="${1:-}" override_body="${2:-}"
	{
		echo '#!/usr/bin/env bash'
		echo 'case "${1:-}" in'
		if [[ -n "$override_name" ]]; then
			echo "${override_name}) ${override_body} ;;"
		fi
		cat <<'EOF'
--version) echo "lintro test 0.0.0"; exit 0 ;;
--help) echo "help"; exit 0 ;;
check|format|test) echo '{"issues": 1}'; exit 1 ;;
doctor) echo '{"tools": []}'; exit 1 ;;
watch) exec 3>&-; echo "Watching for changes in ."; sleep 60 ;;
mcp) echo "the mcp arm is never reached: the driver stub answers"; exit 9 ;;
badge|completions|config|deps|init|install|licenses|list-tools|review|versions) echo "ok $1"; exit 0 ;;
*) echo "unknown command $1" >&2; exit 2 ;;
esac
EOF
	} >"$BINARY"
	chmod +x "$BINARY"
}

teardown() {
	teardown_temp_dir
}

@test "verify_built_binary.sh: --help exits 0" {
	run "$SCRIPT" --help
	assert_success
	assert_output --partial "Verify a built lintro binary"
}

@test "verify_built_binary.sh: missing args exits 2" {
	run "$SCRIPT"
	assert_failure
	assert_equal "2" "$status"
}

@test "verify_built_binary.sh: fails when binary is missing" {
	run "$SCRIPT" "${WORKDIR}/missing"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "Binary not found"
}

@test "verify_built_binary.sh: accepts a healthy binary" {
	write_healthy_binary

	run "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "lintro test 0.0.0"
	assert_output --partial "OK interactive review"
	assert_output --partial "OK mcp round trip"
	assert_output --partial "watch: OK"
}

@test "verify_built_binary.sh: runs every exported command once" {
	write_healthy_binary

	run "$SCRIPT" "$BINARY"
	assert_success
	for name in badge check completions config deps doctor format init \
		install licenses list-tools review test versions; do
		assert_output --partial "${name}: OK"
	done
}

@test "verify_built_binary.sh: --help failure stays non-fatal" {
	write_healthy_binary "--help" 'echo "help exploded"; exit 4'

	run "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "--help exited non-zero"
	assert_output --partial "help exploded"
}

@test "verify_built_binary.sh: fails when --version exits non-zero" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
echo "boom" >&2
exit 3
EOF
	chmod +x "$BINARY"

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_equal "3" "$status"
}

@test "verify_built_binary.sh: fails when the review renders no pygments diff" {
	stub_drivers 1 "FAIL interactive review: the diff rendered without pygments highlighting" \
		0 "OK mcp round trip: lintro 0.0.0 listed 7 tools"
	write_healthy_binary

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "without pygments highlighting"
}

@test "verify_built_binary.sh: fails when the mcp round trip fails" {
	stub_drivers 0 "OK interactive review: pygments-highlighted diff" \
		1 "FAIL mcp round trip: no response to initialize"
	write_healthy_binary

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "FAIL mcp round trip: no response to initialize"
}

@test "verify_built_binary.sh: fails when a command exits with a usage error" {
	write_healthy_binary "versions" 'echo "Error: No such command" >&2; exit 2'

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "versions exited 2 (accepted: 0)"
}

@test "verify_built_binary.sh: fails when a command reports issues but crashed" {
	write_healthy_binary "check" 'echo "Traceback (most recent call last):"; exit 1'

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "check crashed (output contains 'Traceback')"
}

@test "verify_built_binary.sh: fails when a command dies on a missing module" {
	write_healthy_binary "doctor" 'echo "ModuleNotFoundError: No module named '"'"'foo'"'"'" >&2; exit 1'

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "doctor crashed (output contains 'No module named')"
}

@test "verify_built_binary.sh: fails when a lint command exits above 1" {
	write_healthy_binary "format" 'exit 3'

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "format exited 3 (accepted: 0 1)"
}

# The stub closes fd 3 before sleeping: a background process that inherits
# bats' TAP descriptor keeps `run` waiting for it even after the script under
# test has killed and reported it.
@test "verify_built_binary.sh: fails a hung command within the budget" {
	write_healthy_binary "licenses" 'exec 3>&-; sleep 60'

	LINTRO_VERIFY_COMMAND_BUDGET_SECONDS=2 run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "licenses did not exit within 2s"
}

@test "verify_built_binary.sh: fails when watch exits before it is watching" {
	write_healthy_binary "watch" 'echo "watchdog missing" >&2; exit 1'

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "watch exited before it was watching"
	assert_output --partial "watchdog missing"
}

@test "verify_built_binary.sh: fails when watch never becomes ready" {
	write_healthy_binary "watch" 'exec 3>&-; sleep 60'

	LINTRO_VERIFY_COMMAND_BUDGET_SECONDS=2 run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "watch was not ready within 2s"
}

@test "verify_built_binary.sh: runs the commands inside a throwaway workspace" {
	# The stub passes only from inside the gate's own workspace: a fresh git
	# repository holding the staged sample and the fake-provider config.
	write_healthy_binary "config" \
		'[[ -d .git && -f bad.py && -f .lintro-config.yaml ]] || exit 1'

	run "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "config: OK"
	[[ ! -d "${WORKDIR}/.git" ]]
}

@test "verify_built_binary.sh: reports a binary whose directory is missing" {
	run "$SCRIPT" "${WORKDIR}/dist/nuitk/lintro"
	assert_failure
	assert_equal "1" "$status"
	assert_output --partial "Binary not found"
}

@test "verify_built_binary.sh: passes clean output past the crash check" {
	write_healthy_binary "versions" 'echo "lintro 0.0.0 (no traceback here)"; exit 0'

	run "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "versions: OK (exit 0)"
}
