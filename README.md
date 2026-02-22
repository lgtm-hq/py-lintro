# Lintro

<!-- markdownlint-disable MD033 MD013 -->
<p align="center">
<img src="https://raw.githubusercontent.com/lgtm-hq/py-lintro/main/assets/images/lintro.png" alt="Lintro Logo" style="width:100%;max-width:800px;height:auto;">
</p>

<p align="center">
A comprehensive CLI tool that unifies various code formatting, linting, and quality
assurance tools under a single command-line interface.
</p>

<!-- Badges: Build & Quality -->
<p align="center">
<a href="https://github.com/lgtm-hq/py-lintro/actions/workflows/test-and-coverage.yml?query=branch%3Amain"><img src="https://img.shields.io/github/actions/workflow/status/lgtm-hq/py-lintro/test-and-coverage.yml?label=tests&branch=main&logo=githubactions&logoColor=white" alt="Tests"></a>
<a href="https://github.com/lgtm-hq/py-lintro/actions/workflows/ci-pipeline.yml?query=branch%3Amain"><img src="https://img.shields.io/github/actions/workflow/status/lgtm-hq/py-lintro/ci-pipeline.yml?label=ci&branch=main&logo=githubactions&logoColor=white" alt="CI"></a>
<a href="https://github.com/lgtm-hq/py-lintro/actions/workflows/docker-build-publish.yml?query=branch%3Amain"><img src="https://img.shields.io/github/actions/workflow/status/lgtm-hq/py-lintro/docker-build-publish.yml?label=docker&logo=docker&branch=main" alt="Docker"></a>
<a href="https://codecov.io/gh/lgtm-hq/py-lintro"><img src="https://codecov.io/gh/lgtm-hq/py-lintro/branch/main/graph/badge.svg" alt="Coverage"></a>
</p>

<!-- Badges: Releases -->
<p align="center">
<a href="https://github.com/lgtm-hq/py-lintro/releases/latest"><img src="https://img.shields.io/github/v/release/lgtm-hq/py-lintro?label=release" alt="Release"></a>
<a href="https://pypi.org/project/lintro/"><img src="https://img.shields.io/pypi/v/lintro?label=pypi" alt="PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
</p>

<!-- Badges: Security & Supply Chain -->
<p align="center">
<a href="https://github.com/lgtm-hq/py-lintro/actions/workflows/codeql.yml?query=branch%3Amain"><img src="https://github.com/lgtm-hq/py-lintro/actions/workflows/codeql.yml/badge.svg?branch=main" alt="CodeQL"></a>
<a href="https://scorecard.dev/viewer/?uri=github.com/lgtm-hq/py-lintro"><img src="https://api.securityscorecards.dev/projects/github.com/lgtm-hq/py-lintro/badge" alt="OpenSSF Scorecard"></a>
<a href="https://www.bestpractices.dev/projects/11142"><img src="https://www.bestpractices.dev/projects/11142/badge" alt="OpenSSF Best Practices"></a>
<a href="docs/security/assurance.md"><img src="https://img.shields.io/badge/SBOM-CycloneDX-brightgreen" alt="SBOM"></a>
<a href="https://github.com/lgtm-hq/py-lintro/actions/workflows/sbom-on-main.yml?query=branch%3Amain"><img src="https://img.shields.io/github/actions/workflow/status/lgtm-hq/py-lintro/sbom-on-main.yml?label=sbom&branch=main" alt="SBOM Status"></a>
</p>
<!-- markdownlint-enable MD033 MD013 -->

## 🚀 Quick Start

```bash
uv pip install lintro              # Install (or: pip install lintro)
lintro check .                     # Find issues (alias: chk)
lintro format .                    # Fix issues (alias: fmt)
lintro check --output-format grid  # Beautiful output
```

<!-- TODO: Add screenshot of grid output -->

## ✨ Why Lintro?

- **🎯 Unified Interface** - One command for all your linting and formatting tools
- **📊 Consistent Output** - Beautiful, standardized output formats across all tools
- **🔧 Auto-fixing** - Automatically fix issues where possible
- **🐳 Docker Ready** - Run in isolated containers for consistent environments
- **📈 Rich Reporting** - Multiple formats: grid, JSON, HTML, CSV, Markdown
- **⚡ Fast** - Optimized parallel execution

## 🔌 Works With Your Existing Configs

Lintro respects your native tool configurations. If you have a `.prettierrc`,
`pyproject.toml [tool.ruff]`, or `.yamllint`, Lintro uses them automatically - no
migration required.

- **Native configs are detected** - Your existing `.prettierrc`, `.oxlintrc.json`, etc.
  work as-is
- **Enforce settings override consistently** - Set `line_length: 88` once, applied
  everywhere
- **Fallback defaults when needed** - Tools without native configs use sensible defaults

See the [Configuration Guide](docs/configuration.md) for details on the 4-tier config
system.

## 🛠️ Supported Tools

<!-- markdownlint-disable MD013 MD033 MD060 -->

<table>
<thead>
<tr><th>Tool</th><th>Language</th><th>Auto-fix</th><th>Install</th></tr>
</thead>
<tbody>
<tr><th colspan="4">Linters</th></tr>
<tr>
<td><a href="https://github.com/rhysd/actionlint"><img src="https://img.shields.io/badge/Actionlint-24292e?logo=github&logoColor=white" alt="Actionlint"></a></td>
<td>⚙️ GitHub Actions</td>
<td>-</td>
<td><a href="https://github.com/rhysd/actionlint/releases">GitHub Releases</a></td>
</tr>
<tr>
<td><a href="https://github.com/rust-lang/rust-clippy"><img src="https://img.shields.io/badge/Clippy-000000?logo=rust&logoColor=white" alt="Clippy"></a></td>
<td>🦀 Rust</td>
<td>✅</td>
<td><code>rustup component add clippy</code></td>
</tr>
<tr>
<td><a href="https://github.com/hadolint/hadolint"><img src="https://img.shields.io/badge/Hadolint-2496ED?logo=docker&logoColor=white" alt="Hadolint"></a></td>
<td>🐳 Dockerfile</td>
<td>-</td>
<td><a href="https://github.com/hadolint/hadolint/releases">GitHub Releases</a></td>
</tr>
<tr>
<td><a href="https://github.com/DavidAnson/markdownlint-cli2"><img src="https://img.shields.io/badge/Markdownlint--cli2-000000?logo=markdown&logoColor=white" alt="Markdownlint"></a></td>
<td>📝 Markdown</td>
<td>-</td>
<td><code>bun add -g markdownlint-cli2</code><br><code>npm install -g markdownlint-cli2</code></td>
</tr>
<tr>
<td><a href="https://oxc.rs/"><img src="https://img.shields.io/badge/Oxlint-e05d44?logo=javascript&logoColor=white" alt="Oxlint"></a></td>
<td>🟨 JS/TS</td>
<td>✅</td>
<td><code>bun add -g oxlint</code><br><code>npm install -g oxlint</code></td>
</tr>
<tr>
<td><a href="https://github.com/jsh9/pydoclint"><img src="https://img.shields.io/badge/pydoclint-3776AB?logo=python&logoColor=white" alt="pydoclint"></a></td>
<td>🐍 Python</td>
<td>-</td>
<td>📦</td>
</tr>
<tr>
<td><a href="https://www.shellcheck.net/"><img src="https://img.shields.io/badge/ShellCheck-4EAA25?logo=gnubash&logoColor=white" alt="ShellCheck"></a></td>
<td>🐚 Shell Scripts</td>
<td>-</td>
<td><code>brew install shellcheck</code><br><a href="https://github.com/koalaman/shellcheck/releases">GitHub Releases</a></td>
</tr>
<tr>
<td><a href="https://github.com/adrienverge/yamllint"><img src="https://img.shields.io/badge/Yamllint-cb171e?logo=yaml&logoColor=white" alt="Yamllint"></a></td>
<td>🧾 YAML</td>
<td>-</td>
<td>📦</td>
</tr>
<tr><th colspan="4">Formatters</th></tr>
<tr>
<td><a href="https://github.com/psf/black"><img src="https://img.shields.io/badge/Black-000000?logo=python&logoColor=white" alt="Black"></a></td>
<td>🐍 Python</td>
<td>✅</td>
<td>📦</td>
</tr>
<tr>
<td><a href="https://oxc.rs/"><img src="https://img.shields.io/badge/Oxfmt-e05d44?logo=javascript&logoColor=white" alt="Oxfmt"></a></td>
<td>🟨 JS/TS</td>
<td>✅</td>
<td><code>bun add -g oxfmt</code><br><code>npm install -g oxfmt</code></td>
</tr>
<tr>
<td><a href="https://prettier.io/"><img src="https://img.shields.io/badge/Prettier-1a2b34?logo=prettier&logoColor=white" alt="Prettier"></a></td>
<td>🟨 JS/TS · 🧾 JSON</td>
<td>✅</td>
<td><code>bun add -g prettier</code><br><code>npm install -g prettier</code></td>
</tr>
<tr>
<td><a href="https://github.com/mvdan/sh"><img src="https://img.shields.io/badge/shfmt-4EAA25?logo=gnubash&logoColor=white" alt="shfmt"></a></td>
<td>🐚 Shell Scripts</td>
<td>✅</td>
<td><code>brew install shfmt</code><br><a href="https://github.com/mvdan/sh/releases">GitHub Releases</a></td>
</tr>
<tr>
<td><a href="https://github.com/rust-lang/rustfmt"><img src="https://img.shields.io/badge/rustfmt-000000?logo=rust&logoColor=white" alt="rustfmt"></a></td>
<td>🦀 Rust</td>
<td>✅</td>
<td><code>rustup component add rustfmt</code></td>
</tr>
<tr><th colspan="4">Lint + Format</th></tr>
<tr>
<td><a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/badge/Ruff-000?logo=ruff&logoColor=white" alt="Ruff"></a></td>
<td>🐍 Python</td>
<td>✅</td>
<td>📦</td>
</tr>
<tr>
<td><a href="https://sqlfluff.com/"><img src="https://img.shields.io/badge/SQLFluff-4b5563?logo=database&logoColor=white" alt="SQLFluff"></a></td>
<td>🗃️ SQL</td>
<td>✅</td>
<td><code>pipx install sqlfluff</code></td>
</tr>
<tr>
<td><a href="https://taplo.tamasfe.dev/"><img src="https://img.shields.io/badge/Taplo-9b4dca?logo=toml&logoColor=white" alt="Taplo"></a></td>
<td>🧾 TOML</td>
<td>✅</td>
<td><code>brew install taplo</code><br><a href="https://github.com/tamasfe/taplo/releases">GitHub Releases</a></td>
</tr>
<tr><th colspan="4">Type Checkers</th></tr>
<tr>
<td><a href="https://astro.build/"><img src="https://img.shields.io/badge/Astro-ff5d01?logo=astro&logoColor=white" alt="Astro"></a></td>
<td>🚀 Astro</td>
<td>-</td>
<td><code>bun add astro</code><br><code>npm install astro</code></td>
</tr>
<tr>
<td><a href="https://mypy-lang.org/"><img src="https://img.shields.io/badge/Mypy-2d50a5?logo=python&logoColor=white" alt="Mypy"></a></td>
<td>🐍 Python</td>
<td>-</td>
<td>📦</td>
</tr>
<tr>
<td><a href="https://svelte.dev/"><img src="https://img.shields.io/badge/svelte--check-ff3e00?logo=svelte&logoColor=white" alt="svelte-check"></a></td>
<td>🔥 Svelte</td>
<td>-</td>
<td><code>bun add -D svelte-check</code><br><code>npm install -D svelte-check</code></td>
</tr>
<tr>
<td><a href="https://www.typescriptlang.org/"><img src="https://img.shields.io/badge/TypeScript-3178c6?logo=typescript&logoColor=white" alt="TypeScript"></a></td>
<td>🟨 JS/TS</td>
<td>-</td>
<td><code>bun add -g typescript</code><br><code>npm install -g typescript</code><br><code>brew install typescript</code></td>
</tr>
<tr>
<td><a href="https://github.com/vuejs/language-tools"><img src="https://img.shields.io/badge/vue--tsc-42b883?logo=vuedotjs&logoColor=white" alt="vue-tsc"></a></td>
<td>💚 Vue</td>
<td>-</td>
<td><code>bun add -D vue-tsc</code><br><code>npm install -D vue-tsc</code></td>
</tr>
<tr><th colspan="4">Security</th></tr>
<tr>
<td><a href="https://github.com/PyCQA/bandit"><img src="https://img.shields.io/badge/Bandit-yellow?logo=python&logoColor=white" alt="Bandit"></a></td>
<td>🐍 Python</td>
<td>-</td>
<td>📦</td>
</tr>
<tr>
<td><a href="https://gitleaks.io/"><img src="https://img.shields.io/badge/Gitleaks-dc2626?logo=git&logoColor=white" alt="Gitleaks"></a></td>
<td>🔐 Secret Detection</td>
<td>-</td>
<td><code>brew install gitleaks</code><br><a href="https://github.com/gitleaks/gitleaks/releases">GitHub Releases</a></td>
</tr>
<tr>
<td><a href="https://github.com/rustsec/rustsec/tree/main/cargo-audit"><img src="https://img.shields.io/badge/cargo--audit-000000?logo=rust&logoColor=white" alt="cargo-audit"></a></td>
<td>🦀 Rust</td>
<td>-</td>
<td><code>cargo install cargo-audit</code></td>
</tr>
<tr>
<td><a href="https://github.com/EmbarkStudios/cargo-deny"><img src="https://img.shields.io/badge/cargo--deny-000000?logo=rust&logoColor=white" alt="cargo-deny"></a></td>
<td>🦀 Rust</td>
<td>-</td>
<td><code>cargo install cargo-deny</code></td>
</tr>
<tr>
<td><a href="https://semgrep.dev/"><img src="https://img.shields.io/badge/Semgrep-5b21b6?logo=semgrep&logoColor=white" alt="Semgrep"></a></td>
<td>🔒 Multi-language</td>
<td>-</td>
<td><code>pipx install semgrep</code><br><code>pip install semgrep</code><br><code>brew install semgrep</code></td>
</tr>
</tbody>
</table>

> 📦 = bundled with lintro — no separate install needed\
> ⚡ Node.js tools support `--auto-install` to install dependencies automatically

<!-- markdownlint-enable MD013 MD033 MD060 -->

## 🤖 AI-Powered Features (Optional)

Lintro includes optional AI-powered features that provide actionable summaries and
interactive fix suggestions. AI features are **BYO (Bring Your Own) API key** — not
enabled by default.

- **Providers:** Anthropic Claude, OpenAI GPT
- **AI Summary** — high-level assessment with pattern analysis (1 API call per run)
- **Interactive Fix Suggestions** — AI-generated code diffs with risk classification
- **Post-fix Summary** — contextualizes what was fixed and what remains

```bash
# Install with AI support
uv pip install 'lintro[ai]'
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY

# Enable in config
# .lintro-config.yaml
# ai:
#   enabled: true
#   provider: anthropic
```

See the [AI Features Guide](docs/ai-features.md) for full documentation.

## 📦 Installation

**Python 3.11+** is required. Check tool versions with `lintro list-tools`.

```bash
# PyPI (recommended)
uv pip install lintro        # or: pip install lintro

# Homebrew (macOS binary)
brew tap lgtm-hq/tap && brew install lintro-bin

# Docker (tools image - includes all external tools)
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check

# Docker (base image - minimal, no external tools)
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:base check
```

See [Getting Started](docs/getting-started.md) for detailed installation options.

## 💻 Usage

```bash
# Check all files (alias: chk)
lintro check .

# Auto-fix issues (alias: fmt)
lintro format .

# Grid output with grouping
lintro check --output-format grid --group-by file

# Run specific tools
lintro check --tools ruff,prettier,mypy

# Auto-install Node.js dependencies
lintro check --tools tsc --auto-install

# Exclude directories
lintro check --exclude "node_modules,dist,venv"

# List available tools
lintro list-tools
```

### 🐳 Docker

```bash
# Run from GHCR (tools image - recommended)
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check

# With formatting
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:latest check --output-format grid

# Base image (minimal, no external tools)
docker run --rm -v $(pwd):/code ghcr.io/lgtm-hq/py-lintro:base check
```

## 📚 Documentation

| Guide                                            | Description                             |
| ------------------------------------------------ | --------------------------------------- |
| [Getting Started](docs/getting-started.md)       | Installation, first steps, requirements |
| [Configuration](docs/configuration.md)           | Tool configuration, options, presets    |
| [AI Features](docs/ai-features.md)               | AI summaries, fix suggestions, config   |
| [Docker Usage](docs/docker.md)                   | Containerized development               |
| [GitHub Integration](docs/github-integration.md) | CI/CD setup, workflows                  |
| [Contributing](docs/contributing.md)             | Development guide, adding tools         |
| [Troubleshooting](docs/troubleshooting.md)       | Common issues and solutions             |

**Advanced:** [Tool Analysis](docs/tool-analysis/) · [Architecture](docs/architecture/)
· [Security](docs/security/)

## 🔨 Development

```bash
# Clone and install
git clone https://github.com/lgtm-hq/py-lintro.git
cd py-lintro
uv sync --dev

# Run tests
./scripts/local/run-tests.sh

# Run lintro on itself
./scripts/local/local-lintro.sh check --output-format grid
```

## 🤝 Community

- 🐛
  [Bug Reports](https://github.com/lgtm-hq/py-lintro/issues/new?template=bug_report.md)
- 💡
  [Feature Requests](https://github.com/lgtm-hq/py-lintro/issues/new?template=feature_request.md)
- ❓ [Questions](https://github.com/lgtm-hq/py-lintro/issues/new?template=question.md)
- 📖 [Contributing Guide](docs/contributing.md)

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.
