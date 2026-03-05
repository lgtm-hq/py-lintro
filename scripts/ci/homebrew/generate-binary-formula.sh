#!/usr/bin/env bash
# generate-binary-formula.sh
# Generate Homebrew formula for lintro binary distribution

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091 # Can't follow dynamic path; verified at runtime
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Generate Homebrew formula for lintro binary distribution.

Usage: generate-binary-formula.sh <version> <arm64-sha> <x86-sha> <output-file>

Arguments:
  version      Package version (e.g., 1.0.0)
  arm64-sha    SHA256 of the arm64 binary
  x86-sha      SHA256 of the x86_64 binary
  output-file  Path to write the formula (e.g., Formula/lintro-bin.rb)

Examples:
  generate-binary-formula.sh 1.0.0 abc123... def456... Formula/lintro-bin.rb
EOF
	exit 0
fi

VERSION="${1:?Version is required}"
ARM64_SHA="${2:?ARM64 SHA256 is required}"
X86_SHA="${3:?x86_64 SHA256 is required}"
OUTPUT_FILE="${4:?Output file is required}"

log_info "Generating lintro-bin formula for version ${VERSION}"
log_info "ARM64 SHA256: ${ARM64_SHA}"
log_info "x86_64 SHA256: ${X86_SHA}"

cat >"$OUTPUT_FILE" <<EOF
# typed: false
# frozen_string_literal: true

# Homebrew formula for lintro binary distribution
# Auto-generated - do not edit manually
class LintroBin < Formula
  desc "Unified CLI for code quality (binary)"
  homepage "https://github.com/lgtm-hq/py-lintro"
  version "${VERSION}"
  license "MIT"

  RELEASE_BASE = "https://github.com/lgtm-hq/py-lintro/releases"

  on_macos do
    on_arm do
      url "#{RELEASE_BASE}/download/v#{version}/lintro-macos-arm64"
      sha256 "${ARM64_SHA}"
    end
    on_intel do
      url "#{RELEASE_BASE}/download/v#{version}/lintro-macos-x86_64"
      sha256 "${X86_SHA}"
    end
  end

  def install
    if Hardware::CPU.arm?
      bin.install "lintro-macos-arm64" => "lintro"
    else
      bin.install "lintro-macos-x86_64" => "lintro"
    end
  end

  def caveats
    <<~EOS
      lintro-bin is a standalone binary that doesn't require Python.

      The external tools (ruff, black, mypy, etc.) must be installed
      separately:
        brew install ruff black mypy

      For the Python version with bundled tools:
        brew install lgtm-hq/tap/lintro
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/lintro --version")
  end
end
EOF

log_success "Formula written to ${OUTPUT_FILE}"
