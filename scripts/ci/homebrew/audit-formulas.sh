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

Runs `brew style` and `brew audit --strict --formula` against each.
EOF
	exit 0
fi

if ! command -v brew &>/dev/null; then
	log_error "brew is not installed; cannot audit Homebrew formulas"
	exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

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

status=0
for formula in "$FULL_FORMULA" "$BIN_FORMULA"; do
	name="$(basename "$formula")"
	log_info "brew style ${name}"
	if ! brew style "$formula"; then
		log_error "brew style failed for ${name}"
		status=1
	fi

	log_info "brew audit --strict ${name}"
	if ! brew audit --strict --formula "$formula"; then
		log_error "brew audit failed for ${name}"
		status=1
	fi
done

if [[ "$status" -ne 0 ]]; then
	log_error "Homebrew formula validation failed"
	exit 1
fi

log_success "Both Homebrew formulas pass brew style and brew audit"
