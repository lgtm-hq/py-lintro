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
  output-file  Path to write the formula (e.g., Formula/lintro.rb)

Examples:
  generate-binary-formula.sh 1.0.0 abc123... def456... Formula/lintro.rb
EOF
	exit 0
fi

VERSION="${1:?Version is required}"
ARM64_SHA="${2:?ARM64 SHA256 is required}"
X86_SHA="${3:?x86_64 SHA256 is required}"
OUTPUT_FILE="${4:?Output file is required}"

log_info "Generating lightweight lintro formula for version ${VERSION}"
log_info "ARM64 SHA256: ${ARM64_SHA}"
log_info "x86_64 SHA256: ${X86_SHA}"

cat >"$OUTPUT_FILE" <<EOF
# typed: strict
# frozen_string_literal: true

# Homebrew formula for lintro binary distribution
# Auto-generated - do not edit manually
class Lintro < Formula
  desc "Unified CLI for code formatting, linting, and quality assurance"
  homepage "https://github.com/lgtm-hq/py-lintro"
  version "${VERSION}"
  license "MIT"

  # Detect new releases from the GitHub Releases page (the stable URL points at
  # a versioned release asset), keeping this formula in sync with upstream.
  livecheck do
    url :stable
    strategy :github_latest
  end

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

  # Shares the "lintro" binary with the PyPI-based full formula.
  conflicts_with "lintro-full", because: "both provide the lintro binary"

  def install
    if Hardware::CPU.arm?
      bin.install "lintro-macos-arm64" => "lintro"
    else
      bin.install "lintro-macos-x86_64" => "lintro"
    end
  end

  def caveats
    <<~EOS
      lintro is a lightweight standalone binary (no Python required).

      Install tools with:
        lintro doctor
        lintro install --profile recommended

      For all tools bundled via Homebrew dependencies:
        brew install lgtm-hq/tap/lintro-full
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/lintro --version")
    assert_match "Usage:", shell_output("#{bin}/lintro --help")
    # \`lintro doctor\` exits non-zero when optional tools are missing (expected
    # inside the sandboxed test environment), so accept exit status 1.
    assert_match "Lintro Doctor", shell_output("#{bin}/lintro doctor", 1)
  end
end
EOF

log_success "Formula written to ${OUTPUT_FILE}"
