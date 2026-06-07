---
title: 'vision'
description:
  'This document defines the long-term vision, mission, and guiding principles for the
  Lintro project. It serves as the north star for all development decisions.'
category: architecture
order: 10
navGroup: design
---

# Lintro Vision and Mission

This document defines the long-term vision, mission, and guiding principles for the
Lintro project. It serves as the north star for all development decisions.

## Mission Statement

**Lintro aims to be the universal, all-in-one CLI for code quality** - a single tool
that provides linting, formatting, and testing capabilities for ANY modern programming
language, with the highest standards of code quality, usability, and performance.

## Vision

### The Problem We Solve

Modern software development requires numerous code quality tools:

- Python developers need Ruff, Black, Mypy, Bandit, pytest
- JavaScript developers need ESLint, Prettier, Oxlint, Oxfmt
- DevOps engineers need Hadolint, Actionlint, Yamllint
- And the list grows with every language and framework

Each tool has its own:

- Installation process
- Configuration format
- Output format
- CLI interface

**Lintro unifies this fragmented landscape** into a single, consistent experience.

### The Future We're Building

```text
Today (12 tools):          Future (50+ tools):
┌─────────────────┐        ┌─────────────────────────────────┐
│ Python          │        │ Python, JavaScript, TypeScript, │
│ JavaScript      │        │ Go, Rust, Java, C/C++, Ruby,    │
│ YAML, Docker    │        │ PHP, Swift, Kotlin, Scala,      │
│ Markdown        │   →    │ Elixir, Haskell, YAML, Docker,  │
│ GitHub Actions  │        │ Terraform, Kubernetes, SQL,     │
│                 │        │ GraphQL, Protobuf, and more...  │
└─────────────────┘        └─────────────────────────────────┘
```

### Success Criteria

Lintro will be successful when:

1. **Any developer** can install a single tool and get code quality checks for their
   stack
2. **No Python required** - distributed as standalone binaries for all platforms
3. **Consistent experience** - same CLI, same output formats, same configuration
   approach
4. **Exemplary quality** - Lintro itself is the gold standard for code quality
5. **Community-driven** - easy for contributors to add new tool integrations

## Core Principles

These principles are non-negotiable and guide every decision.

### 1. DRY (Don't Repeat Yourself)

Every piece of knowledge must have a single, authoritative representation.

**Applied to Lintro:**

- Tool definitions should be data, not duplicated code
- Parsers should be generic and reusable
- Configuration patterns should be centralized

```python
# BAD: Duplicated logic in every tool
class RuffTool:
    def parse_json_output(self, output): ...

class OxlintTool:
    def parse_json_output(self, output): ...  # Same logic!

# GOOD: Single generic parser
json_parser = create_parser(format="json_lines", field_mapping={...})
```

### 2. SOLID Principles

#### Single Responsibility

Each module, class, and function has one reason to change.

- `parsers/` - only parsing logic
- `formatters/` - only output formatting
- `tools/` - only tool orchestration

#### Open/Closed

Open for extension, closed for modification.

- Adding a new tool should NOT require modifying core code
- Plugin architecture enables this

#### Liskov Substitution

All tools are interchangeable through the `BaseToolPlugin` interface.

#### Interface Segregation

Clients depend only on interfaces they use.

- `ToolResult` provides a minimal, focused interface
- `BaseIssue` defines only what formatters need

#### Dependency Inversion

High-level modules don't depend on low-level modules.

- Core orchestration depends on abstractions (`ToolDefinition`)
- Concrete tools implement those abstractions

### 3. Maintainability Over Cleverness

Code should be readable by developers unfamiliar with the codebase.

**Guidelines:**

- Prefer explicit over implicit
- Avoid overly clever one-liners
- Use descriptive variable and function names
- Write self-documenting code, add comments for "why" not "what"

```python
# BAD: Clever but unclear
if cmd := self._get_cmd() or self._fallback():
    return cmd

# GOOD: Explicit and clear
command = self._get_primary_command()
if command is None:
    command = self._get_fallback_command()
return command
```

### 4. File Size Discipline

Large files are hard to maintain. Break them into logical units.

**Guidelines:**

- Target: < 300 lines per file (soft limit)
- Maximum: 500 lines per file (hard limit)
- If a file grows too large, split by responsibility

```text
# BAD: Monolithic file
tool_executor.py (800 lines)

# GOOD: Split by responsibility
tool_executor/
├── __init__.py
├── executor.py        (execution logic)
├── discovery.py       (file discovery)
├── parallel.py        (parallel execution)
└── result_collector.py (result aggregation)
```

### 5. No Issue Obfuscation

Lintro must pass its own strict linting rules.

**Guidelines:**

- Never disable linter rules without documented justification
- Fix issues properly, don't suppress them
- If a rule seems wrong, discuss removing it project-wide
- The codebase IS the example of good code quality

### 6. Test Everything

Quality without testing is wishful thinking.

**Guidelines:**

- Minimum 70% code coverage (target: 90%)
- Every new feature requires tests
- Every bug fix requires a regression test
- Integration tests for tool workflows

## Quality Standards

### Code Quality Metrics

| Metric                   | Threshold | Target    |
| ------------------------ | --------- | --------- |
| Test Coverage            | ≥ 70%     | 90%       |
| Type Annotation Coverage | ≥ 95%     | 100%      |
| Docstring Coverage       | ≥ 90%     | 100%      |
| File Length              | ≤ 500     | 300 lines |
| Function Length          | ≤ 50      | 25 lines  |
| Cyclomatic Complexity    | ≤ 15      | 10        |

### Self-Validation

Lintro runs on itself. The CI pipeline includes:

```bash
lintro check .       # Must pass with zero issues
lintro format .      # Must result in no changes
lintro test .        # Must pass with coverage threshold
```

### Error Handling Standards

```python
# REQUIRED: Specific exception handling
try:
    result = execute_tool(command)
except subprocess.TimeoutExpired:
    logger.warning(f"Tool {name} timed out after {timeout}s")
    raise ToolTimeoutError(name, timeout) from None
except FileNotFoundError:
    logger.error(f"Tool {name} not found in PATH")
    raise ToolNotFoundError(name) from None

# FORBIDDEN: Silent failures
try:
    result = parse_output(output)
except Exception:
    return None  # Never do this!
```

## Success Metrics

### Short-term (Current → 6 months)

- [ ] Test coverage reaches 70%
- [ ] Error handling audit complete (no silent failures)
- [ ] All 12 current tools have comprehensive documentation
- [ ] Binary distribution via Nuitka or PyOxidizer

### Medium-term (6 months → 1 year)

- [ ] 25+ tool integrations
- [ ] Test coverage reaches 80%
- [ ] Performance benchmarks established
- [ ] Plugin ecosystem for community tools

### Long-term (1+ years)

- [ ] 50+ tool integrations covering all major languages
- [ ] Optional GUI/TUI interface
- [ ] Language server protocol (LSP) integration
- [ ] IDE plugins (VS Code, JetBrains)
- [ ] Test coverage at 90%

## Decision Framework

When making architectural decisions, use this priority order:

1. **Correctness** - Does it work correctly in all cases?
2. **Maintainability** - Can future developers understand and modify it?
3. **Extensibility** - Does it support future growth without major changes?
4. **Performance** - Is it fast enough for the use case?
5. **Elegance** - Is it simple and beautiful?

If two approaches are equal in higher priorities, optimize for lower ones.

## Related Documents

- [ARCHITECTURE.md](./ARCHITECTURE.md) - Technical architecture and design decisions
- [ROADMAP.md](./ROADMAP.md) - Prioritized list of improvements and features
- [../style-guide.md](../style-guide.md) - Coding standards and conventions
- [../contributing.md](../contributing.md) - How to contribute to the project
