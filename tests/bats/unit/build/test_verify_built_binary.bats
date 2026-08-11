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
	assert_output --partial "Binary not found"
}

@test "verify_built_binary.sh: runs --version on a shell script binary" {
	cat >"$BINARY" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
--version) echo "lintro test 0.0.0"; exit 0 ;;
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
