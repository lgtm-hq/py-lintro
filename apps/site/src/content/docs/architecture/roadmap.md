---
title: 'roadmap'
description:
  'This document outlines the prioritized improvements and features for Lintro. Items
  are organized by priority and grouped into logical phases.'
category: architecture
order: 30
navGroup: design
---

# Lintro Roadmap

This document outlines the prioritized improvements and features for Lintro. Items are
organized by priority and grouped into logical phases.

## Priority Legend

- **P0 (Critical):** Blocks further development or user adoption
- **P1 (High):** Significant improvement to quality or usability
- **P2 (Medium):** Nice to have, improves developer experience
- **P3 (Low):** Future consideration, depends on resources

## Current State Assessment

| Area                | Current State    | Target State                      |
| ------------------- | ---------------- | --------------------------------- |
| Test Coverage       | 47%              | 70% (short-term), 90% (long-term) |
| Tool Count          | 12               | 25 (medium-term), 50+ (long-term) |
| Distribution        | PyPI only        | Standalone binaries               |
| Error Handling      | Inconsistent     | Comprehensive, no silent failures |
| Parser Architecture | Per-tool classes | Generic factory                   |

---

## Phase 1: Foundation Strengthening

**Focus:** Fix technical debt, establish quality baseline.

### P0: Error Handling Audit

**Issue:** 20+ instances of broad exception catching, silent failures in parsers.

**Tasks:**

- [ ] Audit all `except Exception` blocks
- [ ] Replace with specific exception types
- [ ] Add logging to all error paths
- [ ] Ensure no silent failures in parsers
- [ ] Document exception hierarchy

**Files to audit:**

```text
lintro/cli.py                    # Line 219: broad except
lintro/parsers/ruff/ruff_parser.py    # Line 91: silent return None
lintro/enums/hadolint_enums.py   # Lines 47, 67: bare except
lintro/tools/implementations/pytest/markers.py  # Line 112: bare except
```

**Acceptance Criteria:**

- Zero `except Exception` without documented justification
- All exceptions logged before handling
- No `return None` in parsers without logging

### P0: Test Coverage to 70%

**Issue:** Current coverage at 47%, particularly weak in CLI (28%).

**Tasks:**

- [ ] Identify untested critical paths
- [ ] Add unit tests for core orchestration
- [ ] Add integration tests for CLI commands
- [ ] Add edge case tests for parsers
- [ ] Set up coverage enforcement in CI

**Priority Areas:**

| Module          | Current | Target | Priority |
| --------------- | ------- | ------ | -------- |
| cli.py          | 28%     | 70%    | P0       |
| plugins/base.py | 55%     | 80%    | P0       |
| tools/core/     | 50%     | 75%    | P1       |
| parsers/        | 60%     | 85%    | P1       |

### P1: Dependency Cleanup

**Issue:** Deprecated and stagnant dependencies.

**Tasks:**

- [ ] Replace `toml` with `tomllib` (Python 3.11+ stdlib)
- [x] Replace `darglint` with `pydoclint` (darglint stagnant since 2021)
- [ ] Audit all dependencies for security advisories
- [ ] Update pinned versions to latest stable

---

## Phase 2: Architecture Evolution

**Focus:** Prepare for scale, reduce maintenance burden.

### P1: Generic Parser Factory

**Issue:** 15+ parser implementations with duplicated patterns.

**Current State:**

```text
parsers/
├── ruff/ruff_parser.py      # JSON lines parsing
├── black/black_parser.py    # Text parsing
├── bandit/bandit_parser.py  # JSON array parsing
└── ... (12 more)
```

**Target State:**

```text
parsers/
├── factory.py               # Parser creation
├── json_lines_parser.py     # Generic JSON lines
├── json_array_parser.py     # Generic JSON array
├── regex_parser.py          # Generic regex-based
└── field_mappings/          # Per-tool config
    ├── ruff.yaml
    └── bandit.yaml
```

**Tasks:**

- [ ] Design parser configuration schema
- [ ] Implement JsonLinesParser with field mapping
- [ ] Implement JsonArrayParser with field mapping
- [ ] Implement RegexParser with capture groups
- [ ] Migrate one tool (Ruff) as proof of concept
- [ ] Migrate remaining tools incrementally

### P1: Tool Definitions as Data

**Issue:** Adding tools requires Python code changes.

**Current State:**

```python
@register_tool
class RuffPlugin(BaseToolPlugin):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="ruff",
            # ... 15 lines of config
        )
```

**Target State:**

```yaml
# tools/definitions/python/ruff.yaml
name: ruff
description: Fast Python linter and formatter
type: linter
languages: [python]
file_patterns: ['*.py', '*.pyi']
commands:
  check: ['ruff', 'check', '--output-format=json']
  fix: ['ruff', 'check', '--fix', '--output-format=json']
parser:
  format: json_lines
  field_mapping:
    code: code
    message: message
    file: filename
    line: location.row
version:
  command: ['ruff', '--version']
  pattern: "ruff (\\d+\\.\\d+\\.\\d+)"
  minimum: '0.14.0'
```

**Tasks:**

- [ ] Design tool definition YAML schema
- [ ] Implement YAML loader with validation
- [ ] Create generic tool executor
- [ ] Migrate Ruff as proof of concept
- [ ] Migrate remaining tools
- [ ] Update contributing guide for new tool format

### P2: Parallel Tool Execution

**Issue:** Tools run sequentially, slow for large codebases.

**Tasks:**

- [ ] Identify independent tool groups (no conflicts)
- [ ] Implement async/concurrent execution
- [ ] Handle result aggregation
- [ ] Add `--parallel` CLI flag
- [ ] Benchmark performance improvement

**Design Consideration:**

```python
# Tools with conflicts must run sequentially
conflict_groups = [
    ["ruff", "flake8"],  # Both lint Python
    ["black", "autopep8"],  # Both format Python
]

# Independent tools can run in parallel
# ruff + prettier + yamllint (different file types)
```

---

## Phase 3: Distribution & Accessibility

**Focus:** Make Lintro accessible without Python.

### P1: Standalone Binary Distribution

**Issue:** Installation requires Python and pip.

#### Approach 1: Nuitka (Recommended for Start)

```bash
# Build command
nuitka --standalone --onefile \
    --include-package=lintro \
    --output-filename=lintro \
    lintro/__main__.py
```

**Tasks:**

- [ ] Add Nuitka to dev dependencies
- [ ] Create build script for all platforms
- [ ] Test on macOS, Linux, Windows
- [ ] Set up CI for binary releases
- [ ] Update homebrew tap formula
- [ ] Create installation docs

#### Approach 2: PyOxidizer (If Nuitka insufficient)

```toml
# pyoxidizer.bzl configuration
[[distribution]]
name = "lintro"
```

### P2: Package Manager Distribution

**Targets:**

- [ ] Homebrew (macOS/Linux) - update existing tap
- [ ] Chocolatey (Windows)
- [ ] Scoop (Windows)
- [ ] APT repository (Debian/Ubuntu)
- [ ] RPM repository (RHEL/Fedora)

### P3: Rust CLI Wrapper (Long-term)

**Rationale:** If performance becomes critical, a thin Rust wrapper could provide:

- Sub-millisecond startup
- Native parallel orchestration
- Smaller binary size

**Tasks:**

- [ ] Create Rust project structure
- [ ] Implement CLI parsing with clap
- [ ] Integrate PyO3 for Python embedding
- [ ] Migrate file discovery to Rust
- [ ] Benchmark against pure Python

---

## Phase 4: Tool Expansion

**Focus:** Grow language coverage systematically.

### P1: Complete Python Ecosystem

**Current:** Ruff, Black, Mypy, Bandit, pydoclint, Pytest

**Add:**

- [ ] `pylint` - comprehensive linter
- [ ] `pyright` - type checker (alternative to mypy)
- [ ] `vulture` - dead code detection
- [ ] `safety` - dependency vulnerability scanning
- [ ] `isort` - import sorting (if not using Ruff)

### P1: JavaScript/TypeScript Ecosystem

**Current:** Prettier, Oxlint, Oxfmt

**Add:**

- [ ] `eslint` - comprehensive linter
- [ ] `tsc` - TypeScript type checking
- [ ] `vitest` / `jest` - test runners

### P2: Go Ecosystem

**Add:**

- [ ] `go vet` - built-in linter
- [ ] `golangci-lint` - meta linter
- [ ] `gofmt` / `gofumpt` - formatters
- [ ] `staticcheck` - advanced static analysis
- [ ] `go test` - test runner

### P2: Rust Ecosystem

**Current:** Clippy

**Add:**

- [ ] `rustfmt` - formatter
- [ ] `cargo test` - test runner
- [ ] `cargo audit` - security scanning

### P3: Additional Languages

**Java/Kotlin:**

- [ ] `checkstyle`
- [ ] `spotless`
- [ ] `ktlint`

**C/C++:**

- [ ] `clang-tidy`
- [ ] `clang-format`
- [ ] `cppcheck`

**Ruby:**

- [ ] `rubocop`
- [ ] `standardrb`

**PHP:**

- [ ] `phpcs`
- [ ] `phpstan`
- [ ] `psalm`

---

## Phase 5: Advanced Features

**Focus:** Enhanced usability and integration.

### P2: Incremental Checking

**Issue:** Full scans are slow for large codebases.

**Design:**

```text
.lintro/
└── cache/
    ├── file_hashes.json    # SHA256 of each file
    └── results/            # Cached results per tool
```

**Tasks:**

- [ ] Implement file hashing
- [ ] Design cache invalidation strategy
- [ ] Add `--no-cache` flag
- [ ] Benchmark improvement

### P2: Watch Mode

**Feature:** Continuous checking on file changes.

```bash
lintro watch --tools ruff,prettier
```

**Tasks:**

- [ ] Integrate file watcher (watchdog)
- [ ] Implement debouncing
- [ ] Design output for continuous mode
- [ ] Handle tool conflicts in watch

### P3: Language Server Protocol (LSP)

**Feature:** IDE integration via standard protocol.

**Tasks:**

- [ ] Research LSP server requirements
- [ ] Implement basic LSP server
- [ ] Support diagnostics publishing
- [ ] Test with VS Code, Neovim

### P3: GUI/TUI Interface

**Feature:** Interactive interface for non-CLI users.

**Options:**

- Textual (Python TUI framework)
- Tauri (Rust + web frontend)
- Electron (if performance acceptable)

---

## Success Metrics

### Short-term (3-6 months)

| Metric               | Target   | Measurement               |
| -------------------- | -------- | ------------------------- |
| Test coverage        | 70%      | CI coverage report        |
| Error handling audit | Complete | Zero bare excepts         |
| Binary distribution  | Working  | Homebrew install succeeds |
| Tools supported      | 15       | Tool count in registry    |

### Medium-term (6-12 months)

| Metric                   | Target   | Measurement                 |
| ------------------------ | -------- | --------------------------- |
| Test coverage            | 80%      | CI coverage report          |
| Generic parser migration | Complete | No per-tool parser code     |
| Tool-as-data migration   | Complete | All tools defined in YAML   |
| Tools supported          | 25       | Tool count in registry      |
| Package managers         | 3+       | Homebrew, Chocolatey, Scoop |

### Long-term (12+ months)

| Metric             | Target      | Measurement             |
| ------------------ | ----------- | ----------------------- |
| Test coverage      | 90%         | CI coverage report      |
| Tools supported    | 50+         | Tool count in registry  |
| Performance        | <1s startup | Benchmark suite         |
| Parallel execution | Implemented | Feature flag            |
| LSP support        | Basic       | VS Code extension works |

---

## Contribution Opportunities

Items marked with good entry points for new contributors:

### Good First Issues

- [ ] Add missing docstrings to public functions
- [ ] Improve error messages for common failures
- [ ] Add test cases for edge cases in parsers
- [ ] Update documentation for clarity

### Medium Complexity

- [ ] Implement a new tool integration (follow existing pattern)
- [ ] Migrate a parser to generic factory
- [ ] Add output format (new style in formatters)
- [ ] Improve CLI help text and examples

### Advanced

- [ ] Design tool definition YAML schema
- [ ] Implement parallel tool execution
- [ ] Create Nuitka build pipeline
- [ ] Implement incremental checking

---

## Related Documents

- [VISION.md](./VISION.md) - Project vision and principles
- [ARCHITECTURE.md](./ARCHITECTURE.md) - Technical architecture
- [../contributing.md](../contributing.md) - How to contribute
- [../style-guide.md](../style-guide.md) - Coding standards
