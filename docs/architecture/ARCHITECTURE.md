# Lintro Architecture

This document describes how Lintro is built: its layers, its main components and the
decisions behind them.

It was checked against Lintro 0.180.34. Sections that describe ideas which are not
implemented say so in their first line, and are collected under
[Future Architecture Considerations](#future-architecture-considerations).

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLI Layer (Click)                              │
│  check, format, test, review, install, doctor, config, init, list-tools,   │
│  versions, baseline, badge, deps, licenses, watch, mcp, completions        │
│  Features: command chaining, aliases, commands imported on first use       │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│                            Orchestration Layer                              │
│  utils/tool_executor.py and utils/execution/: select tools, configure,     │
│  batch, run, verify, aggregate and render                                  │
└──────────┬─────────────────────────┬────────────────────────────────────────┘
           │                         │
┌──────────▼──────────┐   ┌──────────▼──────────┐   ┌─────────────────────────┐
│   Plugin System     │   │  Configuration      │   │   Output Formatting     │
│  - BaseToolPlugin   │   │  - config_loader    │   │   - format_issues       │
│  - ToolRegistry     │   │  - lintro_config    │   │   - Output styles       │
│  - ToolDefinition   │   │  - tool_config      │   │   - BaseIssue model     │
└──────────┬──────────┘   └─────────────────────┘   └─────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────────────────┐
│                           Tool Implementations                              │
│  44 built-in integrations, one package each under lintro/tools/<tool>/     │
│  Each runs its native tool as a subprocess and parses the output           │
└─────────────────────────────────────────────────────────────────────────────┘
```

Three optional surfaces sit beside the CLI and call the same orchestration:

- `lintro/api/`: a library API (`from lintro.api import check, fmt, test`)
- `lintro/mcp/`: an MCP server, started with `lintro mcp`
- `lintro/ai/`: optional AI summaries, fixes and review. The core packages may not
  import it; see [Decision 7](#decision-7-enforced-import-layers)

## Core Components

### 1. Plugin System (`lintro/plugins/`)

The plugin system is how every tool, built-in or third-party, reaches the executor.

#### BaseToolPlugin (`base.py`)

Abstract base class that all tools extend.

**Responsibilities:**

- Subprocess execution with timeout handling
- File discovery and filtering
- Version checking
- Configuration injection
- Working directory management

**Key Methods:**

```python
class BaseToolPlugin(ABC):
    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Return tool metadata."""

    @abstractmethod
    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run tool in check mode."""

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run tool in fix mode (optional)."""

    def set_options(self, **kwargs: Any) -> None:
        """Validate and store tool options."""
```

#### ToolRegistry (`registry.py`)

A class-level registry guarded by a reentrant lock.

**Design Decision:** Lazy instantiation - a tool instance is created the first time it
is requested.

```python
@register_tool
class RuffPlugin(BaseToolPlugin):
    ...

# Later, accessed via:
tool = ToolRegistry.get("ruff")
```

#### ToolDefinition (`protocol.py`)

Dataclass describing tool metadata.

```python
@dataclass
class ToolDefinition:
    name: str                             # Unique identifier
    description: str                      # Human-readable description
    can_fix: bool                         # Supports auto-fix?
    supports_stdin: bool | None           # Can read a file from stdin?
    in_process: bool | None               # Runs inside the Lintro process?
    tool_type: ToolType                   # LINTER, FORMATTER, TYPE_CHECKER, ...
    execution_class: ExecutionClass       # DETERMINISTIC or ADVISORY
    file_patterns: list[str]              # Glob patterns for target files
    claims: list[Claim]                   # Patterns plus what the tool does to them
    reads_tree: bool                      # Runs after mutation settles
    partitionable: bool                   # File set may be sharded
    native_configs: list[str]             # Config files the tool reads
    version_command: list[str] | None     # Command to check version
    min_version: str | None               # Minimum required version
    default_options: dict[str, object]    # Default options
    default_timeout: int                  # Execution timeout in seconds
```

A `DETERMINISTIC` tool runs under `lintro check` and `lintro format`. An `ADVISORY`
tool, such as the AI idiom reviewer, runs only under `lintro review`.

#### Discovery (`discovery.py`)

Built-in tools are loaded from a generated module index,
`lintro/plugins/_builtin_index.py`, joined with a scan of the `lintro.tools` package.
The index exists because a compiled binary has no source directory to scan.

Third-party tools register through the `lintro.tools` entry-point group. External
plugins are not loaded unless the user enables them, with
`LINTRO_ENABLE_EXTERNAL_PLUGINS` or in configuration. A malformed plugin is logged and
skipped. See [plugins](../plugins.md).

### 2. Result Model (`lintro/models/`)

Standardized results let one formatter serve every tool.

#### ToolResult

A frozen dataclass, validated when it is constructed. Its central fields:

```python
@dataclass(frozen=True)
class ToolResult:
    name: str                              # Tool name
    success: bool                          # Did execution succeed?
    output: str | None                     # Raw tool output
    issues_count: int                      # Total issues found
    issues: Sequence[BaseIssue]            # Issue details; defaults to ()
    initial_issues: Sequence[BaseIssue]    # Pre-fix details; defaults to ()
    initial_issues_count: int | None       # Before fixes (if applicable)
    fixed_issues_count: int | None         # Issues auto-fixed
    remaining_issues_count: int | None     # After fixes
    metadata: dict[str, Any]               # Tool metadata; defaults to {}
    pytest_summary: dict[str, Any] | None  # Test-specific data
```

The class carries further state that this listing leaves out, including whether the tool
was skipped or timed out, whether its findings are complete, and how long it ran.
`lintro/models/core/tool_result.py` is the full definition.

#### BaseIssue

Base class for all issue types with unified display mapping. It lives in
`lintro/parsers/base_issue.py`.

**Design Decision:** `DISPLAY_FIELD_MAP` allows subclasses to customize which attributes
map to which display columns without overriding formatting logic.

```python
@dataclass
class BaseIssue:
    file: str
    line: int
    column: int
    message: str
    doc_url: str
    category: str
    fixable: bool

    # Subclasses override this to map their fields
    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {...}

    def to_display_row(self) -> dict[str, str]:
        """Convert to unified display format."""
```

### 3. Parser System (`lintro/parsers/`)

Each tool has a dedicated parser that converts raw output to `BaseIssue` objects.

**Current Structure:**

```text
parsers/
├── base_issue.py          # BaseIssue class
├── base_parser.py         # Helpers shared by the per-tool parsers
├── streaming.py           # Helpers for line-by-line output
├── sarif/                 # SARIF parser and issue type
├── ruff/
│   ├── ruff_parser.py     # Parsing logic
│   └── ruff_issue.py      # RuffIssue dataclass
├── black/
│   ├── black_parser.py
│   └── black_issue.py
└── ...                    # One package per tool
```

One parser per tool is a recorded decision:
[ADR-0001](../adr/0001-native-parser-per-tool.md). A generic parser factory was once
proposed and has not been built; see [Generic Parser Factory](#generic-parser-factory).

### 4. Formatter System (`lintro/formatters/`)

Unified formatting for all tool outputs.

**Components:**

- `formatter.py` - Main `format_issues()` function
- `core/format_registry.py` - Style dispatch
- `styles/` - Output style implementations: plain, grid, Markdown, HTML, JSON, CSV and
  GitHub annotations

`OutputFormat` also names SARIF.

**Design Decision:** Strategy pattern for output styles.

```python
def format_issues(
    issues: Sequence[BaseIssue],
    output_format: OutputFormat | str = OutputFormat.GRID,
    *,
    columns: list[DisplayColumn] | None = None,
    tool_name: str | None = None,
) -> str:
    """Format any BaseIssue subclass consistently."""
```

### 5. Configuration System (`lintro/config/`)

Hierarchical configuration with clear precedence.

**Effective per-tool config precedence (highest to lowest):** this is how a tool's
effective options are resolved (see `lintro/utils/unified_config.py`):

1. CLI `--tool-options` (always wins)
2. `[tool.lintro.<tool>]` per-tool settings in `pyproject.toml`
3. `[tool.lintro]` global settings in `pyproject.toml`
4. Tool native config (e.g. `[tool.ruff]`)
5. Tool defaults

**Config-file source precedence (highest to lowest):** which files supply the
`LintroConfig` model (see `load_config` in `lintro/config/config_loader.py`). Later
tiers are merged over earlier ones key by key:

1. Project config: `.lintro-config.yaml` / `.lintro-config.yml` searched upward from the
   working directory, or the `[tool.lintro]` section of `pyproject.toml` as a fallback
2. User-level global config: `~/.lintro-config.yaml` or its XDG location
3. Built-in defaults (empty configuration)

`load_config()` also takes an explicit path as a function argument. No public CLI
command in 0.180.34 exposes that as a `--config` option, which earlier versions of this
document implied.

> Environment variables are not a general configuration tier. A few have a specific job:
> `LINTRO_GLOBAL_CONFIG` names the user-level config file,
> `LINTRO_ENABLE_EXTERNAL_PLUGINS` permits third-party plugins, `LINTRO_NO_CACHE` clears
> caches, and the AI subsystem reads its own `LINTRO_AI_*` overrides. Others
> (`LINTRO_LOG_DIR`, `LINTRO_VERSION_TIMEOUT`, `LINTRO_DOCKER`) adjust runtime behavior.

**Key Classes:**

- `load_config()` - Finds, merges and loads configuration into `LintroConfig`
- `LintroConfig` - Project-wide settings: `execution`, `enforce`, `defaults`, `tools`,
  `ai`, `review`, `output`, `watch`, `deps` and `baseline`
- `LintroToolConfig` - Per-tool `enabled`, `auto_install` and `rule_packs`. It also
  accepts `config_source`, which 0.180.34 parses and shows in `lintro config` but does
  not pass to any tool

## Design Decisions

### Decision 1: Subprocess-Based Tool Execution

**Choice:** Run tools as subprocesses, not imported Python modules.

**Rationale:**

- Tools may be written in any language (Rust, Go, etc.)
- A tool that crashes does not take Lintro down with it
- Version independence - tools can be updated separately

**Trade-off:** Subprocess overhead vs flexibility. Flexibility wins.

### Decision 2: Thread-Safe Registry

**Choice:** `ToolRegistry` is guarded by a reentrant lock and creates instances lazily.

**Rationale:**

- Supports concurrent access during parallel tool execution
- Lazy loading reduces startup time
- One registry gives every caller the same tool state

Each run works on its own copy of a tool instance, so option changes in one execution do
not leak into another. See [ADR-0002](../adr/0002-per-execution-tool-isolation.md).

### Decision 3: Dataclasses for Results, Pydantic for Configuration {#decision-3-dataclass-heavy-design}

**Choice:** Tool definitions, results and issues are dataclasses. Configuration models
(`LintroConfig`, `LintroToolConfig`) are Pydantic models.

**Rationale:**

- Dataclasses are light, typed, and work well with strict mypy
- Automatic `__repr__` and `__eq__` help debugging
- Pydantic validates user-supplied configuration; `LintroToolConfig` and the top-level
  model reject unknown keys

### Decision 4: BaseIssue with DISPLAY_FIELD_MAP

**Choice:** Single base class with configurable field mapping.

**Rationale:**

- DRY - one formatter handles all tools
- Extensible - new tools add a mapping, not new formatter code
- Testable - mapping logic tested once

### Decision 5: Click for CLI Framework

**Choice:** Click over argparse, Typer, or Fire.

**Rationale:**

- Mature, well-documented, widely adopted
- Good support for command groups
- Decorator-based
- Good testing utilities

**Extension:** The custom `LintroGroup` class adds command chaining and aliases, and
imports a command's module only when that command is first requested.

### Decision 6: One Native Parser per Tool

**Choice:** Each tool's output is parsed by its own parser, in the format the tool emits
natively.

**Rationale and alternatives:** recorded in
[ADR-0001](../adr/0001-native-parser-per-tool.md).

### Decision 7: Enforced Import Layers

**Choice:** The direction of imports between Lintro's packages is checked by two
`import-linter` contracts in `pyproject.toml`.

- **`core-does-not-import-ai`:** the core packages (`config`, `plugins`, `tools`,
  `parsers`, `formatters`, `utils`, `models` and others) may not import `lintro.ai`. The
  AI features stay optional.
- **`layers`**, highest first: `cli_utils | mcp | api`, `ai`, `watch | profiling`,
  `plugins | tools`, `config`, `formatters | parsers`, `utils`, `models | rules`,
  `enums`, `exceptions | licenses | deps`. A package may import from layers below it,
  and packages on one line may not import each other.

Existing violations are listed as exceptions in the contract. That list is meant to
shrink.

## Future Architecture Considerations

This section holds two kinds of entry: ideas that were proposed here and have since been
built, and ideas that have not been built. Each entry says which it is.

### Tool Definitions as Data

**Not built.** Each tool is a Python package whose `definition.py` declares a
`ToolDefinition` and implements the plugin.

The proposal was to describe tools in YAML or TOML files and run them with one generic
executor, so that adding a tool would not need Python code:

```yaml
# Proposal only. No such file exists in Lintro.
name: ruff
type: linter
file_patterns: ['*.py', '*.pyi']
check_command: ['ruff', 'check', '--output-format=json']
parser: json_lines
```

Nothing in 0.180.34 reads a file like this.

### Generic Parser Factory

**Not built.** The proposal was to replace the per-tool parsers with three or four
generic parser types driven by configuration.

The project chose the opposite direction and recorded it:
[ADR-0001](../adr/0001-native-parser-per-tool.md) keeps one native parser per tool, with
shared helpers in `base_parser.py`.

### Parallel Tool Execution

**Built, and on by default.** Independent tools run in parallel. This is controlled by
`execution.parallel` (default `true`) and bounded by `execution.max_workers` (default:
CPU count, clamped 1-32). Tools that must observe each other's writes, and tools that
could rewrite the same file, are grouped into sequential batches by
`get_parallel_batches`, while independent tools run concurrently. Under a mutating
action the batching is handed the run's scan scope so overlap is decided by the files
each tool would actually be given — see
[Write precedence](../configuration.md#write-precedence-overlapping-mutators).
Parallelism only engages when more than one tool is selected. See
`lintro/utils/execution/parallel_executor.py` and `lintro/utils/async_tool_executor.py`.

```python
# Simplified from run_tools_parallel / AsyncToolExecutor
def run_tools_parallel(tools_to_run, paths, action, ..., max_workers):
    # Conflict-aware grouping: no two tools in a batch can write the same file
    batches = get_parallel_batches(tools_to_run, action=action, paths=paths)
    executor = AsyncToolExecutor(max_workers=max_workers)
    results = []
    for batch in batches:
        # Tools within a batch run concurrently on a thread pool
        results.extend(executor.run_tools_parallel(batch, ...))
    return results
```

Disable parallelism with `execution.parallel: false` in `.lintro-config.yaml` when
deterministic sequential ordering is required.

### Distribution Strategy

**Built: option 1 below.** Lintro is published in two forms:

- A Python package on PyPI, which needs a Python runtime (3.11 or later)
- Self-contained binaries compiled with Nuitka, published through npm for macOS on Apple
  Silicon and Linux on x86-64 and ARM64. See [npm distribution](../npm-distribution.md)

The four options this section originally weighed follow, each with its status.

#### Option 1: Nuitka Compilation

**Built.** Python is compiled to a standalone single-file binary. This is how the npm
binaries are produced.

#### Option 2: PyOxidizer

**Not adopted.** It was the alternative packager considered alongside Nuitka.

#### Option 3: Rust CLI Wrapper (Long-term)

**Not built.** The idea was a thin Rust binary that embeds Python through PyO3, for
faster startup. This document records no schedule for it.

#### Option 4: Full Rust Rewrite (Very Long-term)

**Not built.** It was listed as a distant possibility. This document records no schedule
for it.

## Directory Structure

### Current Structure

```text
lintro/
├── __init__.py
├── __main__.py
├── cli.py                   # CLI entry point and LintroGroup
├── cli_utils/
│   └── commands/            # CLI command implementations
├── api/                     # Library API
├── mcp/                     # MCP server
├── ai/                      # Optional AI summaries, fixes and review
├── watch/                   # Watch mode
├── profiling/               # Per-tool timing report (--profile)
├── plugins/                 # Plugin base class, registry, discovery
├── tools/
│   ├── core/                # Tool manager and install helpers
│   ├── ts_checker/          # Base shared by tsc and vue-tsc
│   └── <tool>/              # Per-tool package: definition.py + its helpers
├── config/                  # Configuration models and loading
├── formatters/              # Output formatting
│   ├── core/
│   └── styles/
├── parsers/                 # Tool output parsers, one package per tool
├── utils/                   # Orchestration and shared helpers
│   └── execution/           # Run pipeline: preflight, batching, aggregation
├── models/                  # Data models (ToolResult, etc.)
│   └── core/
├── rules/                   # Packaged rule packs
├── enums/                   # Type-safe enumerations
├── exceptions/              # Custom exception hierarchy
├── licenses/                # License detection and policy
└── deps/                    # Dependency policy
```

### Target Structure (Future)

**Not built.** An earlier version proposed moving orchestration into a `core/` package
and tool definitions into YAML files. Neither has happened, and the YAML proposal
depends on [Tool Definitions as Data](#tool-definitions-as-data). The layout above is
the one to rely on.

## Testing Strategy

### Test Organization

Unit tests mirror the package layout, and each tool has its own integration tests that
run the real binary. [Testing](../testing.md) is the maintained description of the
suite, its markers and its fixtures.

### Coverage Targets

The coverage gate in 0.180.34 fails the run below 81% (`fail_under` in
`pyproject.toml`). The per-component percentages this section used to list were measured
at an early stage and are no longer accurate, so they have been removed.

### Test Patterns

```python
# Unit test - isolated and fast: parse captured output, assert on the issues
def test_parser_extracts_code():
    issues = parse_ruff_output(SAMPLE_JSON)
    assert issues[0].code == "E501"

# Integration test - runs the real tool against a file on disk
def test_ruff_check_finds_issues(tmp_path):
    (tmp_path / "bad.py").write_text("import os\n")
    result = ToolRegistry.get("ruff").check([str(tmp_path)], {})
    assert result.issues_count > 0
```

These snippets show the two shapes of test. They are illustrative, not copied from the
suite.

## Performance Considerations

### Current Bottlenecks

1. **Subprocess overhead** - Each tool spawns a new process
2. **Full file discovery** - By default every run discovers and checks all matching
   files. Checking only changed files is opt-in (below)

### Optimization Roadmap

1. **Parallel execution** - Run independent tools concurrently — **implemented**
   (`execution.parallel`, on by default; conflict-aware batching)
2. **Incremental checking** - **implemented, opt-in**: `lintro check --incremental`
   checks only files changed since the last run, using a cache of file modification
   times and sizes. `--no-cache` clears it
3. **Lazy command loading** - **implemented**: a command's module is imported only when
   that command runs
4. **Subprocess pooling** - Reuse processes for the same tool — **not built**

See the [performance epic (#597–#601)](https://github.com/lgtm-hq/py-lintro/issues/597)
for the history of this work.

### Performance Metrics {#performance-metrics-to-establish}

`lintro check --profile` reports the wall-clock time each tool took in a run. The
project publishes no benchmark figures, so this document makes no speed claims.

## Security Considerations

### Current Measures

- Tools run with `shell=False`, and the command name is validated before execution
- `defusedxml` parses the XML that cppcheck and pytest emit
- External plugins are not loaded unless the user enables them
- Prompts that Lintro assembles for an AI provider pass through secret redaction. One
  documented option delegates diff retrieval to the provider and bypasses it; see
  [AI features](../ai-features.md#what-is-not-sent)

The project's supply-chain practices, including static analysis and SBOM generation, are
described in [security assurance](../security/assurance.md).

### Subprocess Security

```python
# Expected: an argument list, no shell
subprocess.run(
    [tool_path, "--check", *validated_paths],
    capture_output=True,
    timeout=timeout,
    cwd=working_directory,
)

# Never: a shell string built from input
subprocess.run(
    f"{tool} {user_input}",
    shell=True,
)
```

## Related Documents

- [VISION.md](./VISION.md) - Project vision and guiding principles
- [ROADMAP.md](./ROADMAP.md) - The original roadmap, kept as a historical record
- [../style-guide.md](../style-guide.md) - Coding standards
- [../contributing.md](../contributing.md) - Contribution guidelines
- [../adr/README.md](../adr/README.md) - Architecture Decision Records index
- [ADR-0008](../adr/0008-ai-review-architecture-invariants.md) - Invariants the AI
  review subsystem holds across the #1972 decomposition
