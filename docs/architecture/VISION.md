# Lintro Vision and Mission

This document states what Lintro is for, the principles its code is held to, and how far
the project has come against its original goals.

Statements about what exists were checked against Lintro 0.180.34. Anything under
[Not yet built](#not-yet-built) is an intention with no release date; do not read it as
a shipped feature.

## Mission Statement

**Lintro aims to be one CLI for code quality across the languages in a project.** It
runs linters, formatters, type checkers, security scanners and tests behind the same
commands, the same configuration approach and the same output formats.

Lintro orchestrates native tools; it does not reimplement them. Each tool keeps its own
rules, and its own configuration file when it has one.

## Vision

### The Problem We Solve

A project of any size needs several code quality tools:

- Python code is checked with Ruff, Black, mypy, Bandit and pytest
- JavaScript and TypeScript code is checked with Prettier, Oxlint, Oxfmt and tsc
- Infrastructure files are checked with Hadolint, Actionlint and Yamllint

Each tool has its own:

- Installation process
- Configuration format
- Output format
- CLI interface

Lintro puts one interface in front of them: `lintro check`, `lintro format` and
`lintro test`, with one way to select tools, one configuration model and one set of
output formats.

### The Future We're Building

The first version of this document compared 12 tools "today" with "50+" in the future.
That picture is out of date, so this section now records where the project stands.

**Today, in 0.180.34:** 44 built-in tool integrations. They cover Python, JavaScript and
TypeScript (including Vue, Svelte and Astro type checking), Rust, Go, C and C++, shell,
SQL, CSS, HTML, Markdown, YAML, TOML, Dockerfiles, GitHub Actions workflows, Terraform,
Protobuf and OpenAPI documents, plus prose and spelling checks, secret detection and
dependency audits. The [tool catalog](../tool-analysis/README.md) lists every
integration.

**Not integrated:** there is no dedicated tool for Java, Ruby, PHP, Swift, Kotlin,
Scala, Elixir, Haskell or Kubernetes manifests. The original "50+ tools" figure was an
ambition, not a schedule.

### Success Criteria

The original criteria, with their status in 0.180.34:

1. **One install for a whole stack: partly met.** One Lintro install provides the CLI.
   The native tools are separate programs: `lintro install` installs the ones Lintro
   knows how to install, and `lintro doctor` reports what is missing. A tool that is not
   installed is skipped, and a skipped tool has not checked anything.
2. **No Python required: met on three platforms.** Self-contained binaries are published
   through npm for macOS on Apple Silicon and for Linux on x86-64 and ARM64. Intel Macs
   install through Homebrew or PyPI, which do need Python. See
   [npm distribution](../npm-distribution.md).
3. **Consistent experience: met.** Every tool is reached through the same commands,
   tool-selection options and output formats.
4. **Lintro holds itself to its own rules: ongoing.** The project runs Lintro on its own
   source. The enforced gates are listed under [Quality Standards](#quality-standards).
5. **Open to community tools: met, opt-in.** A third-party package can register a tool
   through the `lintro.tools` entry-point group. Loading external plugins is off until
   the user enables it. See [plugins](../plugins.md).

## Core Principles

These principles guide design and review. Where a principle is enforced by a gate, the
gate is named; where it is a convention, it says so.

### 1. DRY (Don't Repeat Yourself)

Every piece of knowledge should have one authoritative home.

**Applied to Lintro:**

- A tool's metadata lives in one `ToolDefinition`: its file patterns, native config
  files, minimum version and default options
- Parsers share helpers in `lintro/parsers/base_parser.py`
- `tsc` and `vue-tsc` share one checker base in `lintro/tools/ts_checker/`
- Duplicate code in the per-tool packages is gated: pylint's `duplicate-code` check may
  not report more clone sets than the recorded baseline, which is zero in this release

Each tool still has its own parser. That is a recorded decision, not an oversight: see
[ADR-0001](../adr/0001-native-parser-per-tool.md).

### 2. SOLID Principles

#### Single Responsibility

Each module, class and function has one reason to change.

- `parsers/` turns native tool output into issues
- `formatters/` renders issues for people and machines
- `tools/` holds one package per integration

#### Open/Closed

Adding a tool should not require editing the orchestration code. A new integration is a
package with a `definition.py` that registers itself with `@register_tool`.

#### Liskov Substitution

Every tool is used through the `BaseToolPlugin` interface, so the executor treats them
alike.

#### Interface Segregation

Formatters depend on `BaseIssue`, not on any tool's own issue class. A tool maps its
extra fields to display columns through `DISPLAY_FIELD_MAP`.

#### Dependency Inversion

Orchestration depends on `ToolDefinition` and `BaseToolPlugin`, not on concrete tools.
The direction of imports between packages is enforced by `import-linter` contracts,
described in the
[architecture document](./ARCHITECTURE.md#decision-7-enforced-import-layers).

### 3. Maintainability Over Cleverness

Code should be readable by a developer who has not seen the codebase before. This is a
review convention.

**Guidelines:**

- Prefer explicit over implicit
- Avoid clever one-liners
- Use descriptive variable and function names
- Comment the reason, not the mechanics

```python
# Harder to follow: two lookups hidden in one condition
if cmd := self._get_cmd() or self._fallback():
    return cmd

# Easier to follow: each step is named
command = self._get_primary_command()
if command is None:
    command = self._get_fallback_command()
return command
```

### 4. File Size Discipline

Large files are hard to maintain, so they are split by responsibility.

**What is enforced in 0.180.34:** a module-size gate warns when a Python module passes
800 lines. Modules already over the limit sit in a baseline list that is meant to
shrink, and the gate reports without failing the run. The settings are in
`[tool.lintro.module_size]`.

**What is still an aim:** the original goal was 300 lines, with 500 as a ceiling. The
gate is not that strict yet. The stated plan is to lower the threshold in steps once the
baseline list is empty.

### 5. No Issue Obfuscation

Lintro should pass its own rules without hiding findings.

**Guidelines:**

- Do not disable a rule without a documented reason
- Fix the issue instead of suppressing it
- If a rule is wrong for the project, change it for the whole project
- Unused suppressions are themselves reported: Ruff's `RUF100` is enabled

### 6. Test Everything

**Guidelines:**

- Every new feature needs tests
- Every bug fix needs a regression test
- Tool integrations have integration tests that run the real tool
- The coverage gate fails the run below 81%

See [testing](../testing.md) for how the suite is organised.

## Quality Standards

### Code Quality Metrics

These gates are configured in `pyproject.toml` in 0.180.34. Each is a limit the build
enforces, not a target.

| Metric                      | Enforced limit                        |
| --------------------------- | ------------------------------------- |
| Test coverage               | At least 81% (`fail_under`)           |
| Cyclomatic complexity       | At most 15 per function (Ruff `C90`)  |
| Function arguments          | At most 8 (Ruff `PLR0913`)            |
| Branches per function       | At most 12 (Ruff `PLR0912`)           |
| Statements per function     | At most 50 (Ruff `PLR0915`)           |
| Module length               | Warning above 800 lines               |
| Duplicate code in tool code | No clone sets above the baseline of 0 |
| Import direction            | Two `import-linter` contracts         |
| Docstrings                  | Google style, checked by pydoclint    |

Functions that exceeded a structural limit when the rule was switched on are recorded as
per-file exceptions. That list is meant to shrink. The recorded plan for complexity is
to tighten the limit from 15 to 12, then to 10.

The first version of this table also listed type-annotation and docstring coverage
percentages and a 25-line function target. No gate in 0.180.34 measures those figures,
so they are no longer stated here.

### Self-Validation

Lintro runs on its own source:

```bash
lintro check .       # Report issues
lintro format .      # Apply supported fixes
lintro test          # Run the test suite
```

### Error Handling Standards

Failures should be specific and visible. Catch the exception you expect, log what
happened, and raise an error from Lintro's own hierarchy (`LintroError` and its
subclasses, such as `ToolTimeoutError` and `ToolExecutionError`).

```python
# Expected: a specific exception, logged and re-raised as a Lintro error
try:
    result = execute_tool(command)
except subprocess.TimeoutExpired:
    logger.warning(f"Tool {name} timed out after {timeout}s")
    raise ToolTimeoutError(f"{name} timed out after {timeout}s") from None

# Not acceptable: a silent failure
try:
    result = parse_output(output)
except Exception:
    return None
```

The snippet illustrates the convention. It is not copied from the source tree.

## Success Metrics

The first version of this document set time-boxed goals. They are regrouped here by what
has happened.

### Reached

- Test coverage passed the original 70% goal; the gate is now 81%
- Tool integrations passed the original goal of 25; there are 44
- Standalone binaries ship, built with Nuitka
- Community tools can register through plugin entry points
- Independent tools run in parallel by default

### Not yet built

Nothing in this list exists in 0.180.34, and none of it has a committed date:

- 50 or more tool integrations
- A GUI or terminal UI
- Language Server Protocol integration
- Editor plugins for VS Code or JetBrains
- Test coverage at 90%

Current priorities are tracked in the
[ordered GitHub backlog](https://github.com/lgtm-hq/py-lintro/issues/2288), not in this
document.

## Decision Framework

When making architectural decisions, use this priority order:

1. **Correctness** - Does it work correctly in all cases?
2. **Maintainability** - Can future developers understand and modify it?
3. **Extensibility** - Does it support future growth without major changes?
4. **Performance** - Is it fast enough for the use case?
5. **Elegance** - Is it simple?

If two approaches are equal on a higher priority, decide on the next one.

## Related Documents

- [ARCHITECTURE.md](./ARCHITECTURE.md) - How the code is organised today
- [ROADMAP.md](./ROADMAP.md) - The original roadmap, kept as a historical record
- [../style-guide.md](../style-guide.md) - Coding standards and conventions
- [../contributing.md](../contributing.md) - How to contribute to the project
