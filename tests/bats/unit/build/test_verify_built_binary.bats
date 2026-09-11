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
	# The verify step drives the pty review through `python3`; stub it so the
	# cases below exercise the shell branches against a stub binary (#2514).
	STUB_BIN="${BATS_TEST_TMPDIR}/stub-bin"
	mkdir -p "$STUB_BIN"
	stub_review_driver 0 "OK interactive review: pygments-highlighted diff"
	PATH="${STUB_BIN}:${PATH}"
	export PATH
}

# Write a fake `python3` standing in for drive_interactive_review.py.
#
# Args:
#   $1: exit status the driver should report.
#   $2: line the driver should print.
stub_review_driver() {
	cat >"${STUB_BIN}/python3" <<EOF
#!/usr/bin/env bash
case "\${1:-}" in
*drive_interactive_review.py) echo "$2"; exit $1 ;;
*) exit 0 ;;
esac
EOF
	chmod +x "${STUB_BIN}/python3"
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

@test "verify_built_binary.sh: runs --version on a shell script binary" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
mcp) echo "  --workspace DIRECTORY  Workspace root for path guards"; exit 0 ;;
--help) echo "help"; exit 0 ;;
*) exit 1 ;;
esac
EOF
	chmod +x "$BINARY"

	run "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "lintro test 0.0.0"
}

@test "verify_built_binary.sh: --help failure stays non-fatal" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
mcp) echo "  --workspace DIRECTORY  Workspace root for path guards"; exit 0 ;;
*) echo "help exploded"; exit 4 ;;
esac
EOF
	chmod +x "$BINARY"

	run "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "lintro test 0.0.0"
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

@test "verify_built_binary.sh: runs the interactive review driver" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
mcp) echo "  --workspace DIRECTORY  Workspace root for path guards"; exit 0 ;;
--help) echo "help"; exit 0 ;;
*) exit 1 ;;
esac
EOF
	chmod +x "$BINARY"

	run "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "OK interactive review"
}

@test "verify_built_binary.sh: fails when the review renders no pygments diff" {
	stub_review_driver 1 "FAIL interactive review: the diff rendered without pygments highlighting"
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
mcp) echo "  --workspace DIRECTORY  Workspace root for path guards"; exit 0 ;;
*) exit 0 ;;
esac
EOF
	chmod +x "$BINARY"

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "without pygments highlighting"
}





@test "verify_built_binary.sh: accepts a binary whose mcp command renders help" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
mcp) [[ "${2:-}" == "--help" ]] || exit 9
	echo "  --workspace DIRECTORY  Workspace root for path guards"; exit 0 ;;
--help) echo "help"; exit 0 ;;
*) exit 1 ;;
esac
EOF
	chmod +x "$BINARY"

	run "$SCRIPT" "$BINARY"
	assert_success
	assert_output --partial "MCP command is wired into the binary"
}

@test "verify_built_binary.sh: fails when the mcp command is missing" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
mcp) echo "Error: No such command 'mcp'." >&2; exit 2 ;;
--help) echo "help"; exit 0 ;;
*) exit 1 ;;
esac
EOF
	chmod +x "$BINARY"

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "mcp command wiring failed (exit 2)"
}

@test "verify_built_binary.sh: fails when mcp --help renders no options" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
mcp) echo ""; exit 0 ;;
--help) echo "help"; exit 0 ;;
*) exit 1 ;;
esac
EOF
	chmod +x "$BINARY"

	run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "mcp command wiring failed (exit 0)"
}

# The stub closes fd 3 before sleeping: a background process that inherits
# bats' TAP descriptor keeps `run` waiting for it even after the script under
# test has killed and reported it.
@test "verify_built_binary.sh: fails a hung mcp command within the budget" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
mcp) exec 3>&-; sleep 120 ;;
--help) echo "help"; exit 0 ;;
*) exit 1 ;;
esac
EOF
	chmod +x "$BINARY"

	LINTRO_VERIFY_MCP_BUDGET_SECONDS=2 run "$SCRIPT" "$BINARY"
	assert_failure
	assert_output --partial "mcp --help did not exit within 2s"
}
