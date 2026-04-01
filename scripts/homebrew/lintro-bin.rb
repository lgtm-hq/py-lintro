# typed: false
# frozen_string_literal: true

# Homebrew formula for lintro binary distribution
# This installs a pre-compiled binary that doesn't require Python
class LintroBin < Formula
  desc "Unified CLI for code quality (binary)"
  homepage "https://github.com/lgtm-hq/py-lintro"
  version "0.22.0"
  license "MIT"

  RELEASE_BASE = "https://github.com/lgtm-hq/py-lintro/releases"

  on_macos do
    on_arm do
      url "#{RELEASE_BASE}/download/v#{version}/lintro-macos-arm64"
      sha256 "PLACEHOLDER_ARM64_SHA256"
    end
    on_intel do
      url "#{RELEASE_BASE}/download/v#{version}/lintro-macos-x86_64"
      sha256 "PLACEHOLDER_X86_64_SHA256"
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
      External tools must be installed separately.

      Run 'lintro doctor' to see which tools are available and which
      are missing, with install hints for each.

      Quick setup — install common tools via Homebrew:
        brew install ruff black mypy bandit yamllint \\
          shellcheck shfmt prettier hadolint actionlint \\
          gitleaks semgrep markdownlint-cli2 taplo sqlfluff

      JS / TS tools (via bun or npm):
        bun add -g oxlint oxfmt       # if using bun
        npm install -g oxlint oxfmt   # if using npm

      Rust tools (requires rustup, not Homebrew rust):
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
        rustup component add clippy rustfmt
        cargo install cargo-audit cargo-deny

      For the Python-based version with all tools bundled:
        brew install lgtm-hq/tap/lintro
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/lintro --version")
    assert_match "Usage:", shell_output("#{bin}/lintro --help")
  end
end
