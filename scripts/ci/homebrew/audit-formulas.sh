#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Render both Homebrew formulas with placeholder values and validate
#          them with `brew style` and `brew audit --strict`.
#
# The source template (templates/lintro.rb.template) and the binary formula
# generator (generate-binary-formula.sh) contain placeholders and cannot be
# audited directly. This script renders both with dummy-but-well-formed values
# so Homebrew's own linters (RuboCop style + audit) can check structure,
# component ordering, and required fields without needing real release assets.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Validate the rendered Homebrew formulas with brew style and brew audit.

Usage: audit-formulas.sh

Requires: Homebrew (brew) and Python 3 on PATH.

Renders:
  - Formula/lintro-full.rb from templates/lintro.rb.template (PyPI + resources)
  - Formula/lintro.rb from generate-binary-formula.sh (standalone binary)

Runs `brew style` on each rendered file and `brew audit --strict --formula`
  by name via an ephemeral local tap (Homebrew no longer accepts paths).
EOF
	exit 0
fi

if ! command -v brew &>/dev/null; then
	log_error "brew is not installed; cannot audit Homebrew formulas"
	exit 1
fi

WORK_DIR="$(mktemp -d)"

# 64-char hex placeholder SHA256 values (structurally valid, not real).
DUMMY_SHA_A="$(printf 'a%.0s' {1..64})"
DUMMY_SHA_B="$(printf 'b%.0s' {1..64})"
DUMMY_SHA_C="$(printf 'c%.0s' {1..64})"
DUMMY_SHA_D="$(printf 'd%.0s' {1..64})"

log_info "Rendering PyPI formula (lintro-full.rb) with placeholder values"
cat >"$WORK_DIR/poet.txt" <<EOF
  resource "example" do
    url "https://files.pythonhosted.org/packages/aa/example-1.0.0.tar.gz"
    sha256 "${DUMMY_SHA_A}"
  end
EOF
cat >"$WORK_DIR/pydoclint.txt" <<EOF
  resource "pydoclint" do
    url "https://files.pythonhosted.org/packages/bb/pydoclint-0.1.0-py3-none-any.whl"
    sha256 "${DUMMY_SHA_B}"
  end
EOF
cat >"$WORK_DIR/pydantic.txt" <<EOF
  resource "pydantic_core" do
    url "https://files.pythonhosted.org/packages/cc/pydantic_core-2.0.0-cp313-cp313-macosx_11_0_arm64.whl"
    sha256 "${DUMMY_SHA_C}"
  end
EOF

FULL_FORMULA="$WORK_DIR/lintro-full.rb"
python3 "$SCRIPT_DIR/render_formula.py" \
	--tarball-url "https://files.pythonhosted.org/packages/dd/lintro-0.0.0.tar.gz" \
	--tarball-sha "$DUMMY_SHA_D" \
	--poet-resources "$WORK_DIR/poet.txt" \
	--pydoclint-resource "$WORK_DIR/pydoclint.txt" \
	--pydantic-resource "$WORK_DIR/pydantic.txt" \
	--output "$FULL_FORMULA"

log_info "Generating binary formula (lintro.rb) with placeholder values"
BIN_FORMULA="$WORK_DIR/lintro.rb"
"$SCRIPT_DIR/generate-binary-formula.sh" \
	"0.0.0" "$DUMMY_SHA_A" "$DUMMY_SHA_B" "$BIN_FORMULA"

# Homebrew disabled `brew audit [path ...]`; audit by formula name via an
# ephemeral local tap, then clean it up.
TAP_USER="lgtm-ci"
TAP_REPO="lintro-audit"
TAP_PATH="$(brew --repository)/Library/Taps/${TAP_USER}/homebrew-${TAP_REPO}"
mkdir -p "${TAP_PATH}/Formula"
cp "$FULL_FORMULA" "${TAP_PATH}/Formula/lintro-full.rb"
cp "$BIN_FORMULA" "${TAP_PATH}/Formula/lintro.rb"
trap 'rm -rf "$WORK_DIR" "$TAP_PATH"' EXIT

status=0
for name in lintro-full lintro; do
	formula_path="${TAP_PATH}/Formula/${name}.rb"
	qualified="${TAP_USER}/${TAP_REPO}/${name}"

	log_info "brew style ${name}"
	if ! brew style "$formula_path"; then
		log_error "brew style failed for ${name}"
		status=1
	fi

	log_info "brew audit --strict ${qualified}"
	if ! brew audit --strict --formula "$qualified"; then
		log_error "brew audit failed for ${name}"
		status=1
	fi
done

if [[ "$status" -ne 0 ]]; then
	log_error "Homebrew formula validation failed"
	exit 1
fi

log_success "Both Homebrew formulas pass brew style and brew audit"
