# Lintro Architecture

This document describes the technical architecture of Lintro, including design
decisions, component relationships, and guidelines for future development.

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLI Layer (Click)                              │
│  Commands: check, format, test, list-tools, config, init, versions         │
│  Features: command chaining, aliases, rich output                           │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│                            Orchestration Layer                              │
│  tool_executor.py - coordinates tool discovery, execution, result handling │
└──────────┬─────────────────────────┬────────────────────────────────────────┘
           │                         │
┌──────────▼──────────┐   ┌──────────▼──────────┐   ┌─────────────────────────┐
│   Plugin System     │   │  Configuration      │   │   Output Formatting     │
│  - BaseToolPlugin   │   │  - config_loader    │   │   - Unified formatter   │
│  - ToolRegistry     │   │  - lintro_config    │   │   - Multiple styles     │
│  - ToolDefinition   │   │  - tool_config      │   │   - BaseIssue model     │
└──────────┬──────────┘   └───────────────────────┘   └─────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────────────────┐
│                           Tool Implementations                              │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │  Ruff   │ │  Black  │ │  Mypy   │ │ Bandit  │ │Prettier │ │ Oxlint  │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘   │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │Yamllint │ │Hadolint │ │Actionlint│ │Markdown │ │ Clippy  │ │ Pytest  │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Plugin System (`lintro/plugins/`)

The plugin system is the foundation for tool extensibility.

#### BaseToolPlugin (`base.py`)

Abstract base class that all tools must extend.

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
    def check(self, paths: list[str], options: dict) -> ToolResult:
        """Run tool in check mode."""

    def fix(self, paths: list[str], options: dict) -> ToolResult:
        """Run tool in fix mode (optional)."""
```

#### ToolRegistry (`registry.py`)

Thread-safe singleton registry for tool discovery and access.

**Design Decision:** Lazy instantiation - tools are only created when first accessed.

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
    name: str                    # Unique identifier
    description: str             # Human-readable description
    can_fix: bool               # Supports auto-fix?
    tool_type: ToolType         # LINTER, FORMATTER, TYPE_CHECKER, etc.
    file_patterns: list[str]    # Glob patterns for target files
    priority: int               # Execution order (higher = first)
    conflicts_with: list[str]   # Mutually exclusive tools
    native_configs: list[str]   # Config files the tool reads
    version_command: list[str]  # Command to check version
    min_version: str | None     # Minimum required version
    default_options: dict       # Default CLI options
    default_timeout: int        # Execution timeout in seconds
```

### 2. Result Model (`lintro/models/`)

Standardized results enable unified formatting across all tools.

#### ToolResult

```python
@dataclass
class ToolResult:
    name: str                              # Tool name
    success: bool                          # Did execution succeed?
    issues_count: int                      # Total issues found
    issues: Sequence[BaseIssue] | None     # Issue details
    initial_issues_count: int | None       # Before fixes (if applicable)
    fixed_issues_count: int | None         # Issues auto-fixed
    remaining_issues_count: int | None     # After fixes
    pytest_summary: PytestSummary | None   # Test-specific data
```

#### BaseIssue

Base class for all issue types with unified display mapping.

**Design Decision:** `DISPLAY_FIELD_MAP` allows subclasses to customize which attributes
map to which display columns without overriding formatting logic.

```python
@dataclass
class BaseIssue:
    file: str
    line: int
    column: int | None
    message: str

    # Subclasses override this to map their fields
    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        "code": "code",
        "severity": "severity",
    }

    def to_display_row(self) -> dict[str, str]:
        """Convert to unified display format."""
```

### 3. Parser System (`lintro/parsers/`)

Each tool has a dedicated parser that converts raw output to `BaseIssue` objects.

**Current Structure:**

```text
parsers/
├── base_issue.py          # BaseIssue class
├── ruff/
│   ├── ruff_parser.py     # Parsing logic
│   └── ruff_issue.py      # RuffIssue dataclass
├── black/
│   ├── black_parser.py
│   └── black_issue.py
└── ...
```

**Future Direction:** Generic parser factory to reduce duplication.

```python
# Target architecture (not yet implemented)
def create_parser(
    format: Literal["json_lines", "json_array", "regex"],
    field_mapping: dict[str, str],
) -> Callable[[str], list[BaseIssue]]:
    """Create a parser from configuration, not code."""
```

### 4. Formatter System (`lintro/formatters/`)

Unified formatting for all tool outputs.

**Components:**

- `formatter.py` - Main `format_issues()` function
- `core/format_registry.py` - Style dispatch
- `styles/` - Output style implementations (grid, JSON, HTML, CSV, Markdown)

**Design Decision:** Strategy pattern for output styles.

```python
def format_issues(
    issues: Sequence[BaseIssue],
    output_format: OutputFormat = OutputFormat.GRID,
    **kwargs,
) -> str:
    """Format any BaseIssue subclass consistently."""
```

### 5. Configuration System (`lintro/config/`)

Hierarchical configuration with clear precedence.

**Precedence (highest to lowest):**

1. CLI flags
2. Environment variables
3. `.lintro-config.yaml` / `.lintro-config.yml`
4. `pyproject.toml` `[tool.lintro]` section (fallback)
5. Tool native configs (`.ruff.toml`, etc.)
6. Hardcoded defaults

**Key Classes:**

- `ConfigLoader` - Finds and loads configuration
- `LintroConfig` - Project-wide settings
- `ToolConfig` - Per-tool settings

## Design Decisions

### Decision 1: Subprocess-Based Tool Execution

**Choice:** Run tools as subprocesses, not imported Python modules.

**Rationale:**

- Tools may be written in any language (Rust, Go, etc.)
- Isolation prevents tool crashes from affecting Lintro
- Version independence - tools can be updated separately
- Security - tools run with limited permissions

**Trade-off:** Subprocess overhead vs flexibility. Flexibility wins.

### Decision 2: Thread-Safe Registry

**Choice:** `ToolRegistry` is a thread-safe singleton with lazy instantiation.

**Rationale:**

- Supports concurrent access in future parallel execution
- Lazy loading reduces startup time
- Singleton ensures consistent tool state

### Decision 3: Dataclass-Heavy Design

**Choice:** Use `@dataclass` extensively for models, configs, and results.

**Rationale:**

- Type safety with `field()` defaults
- Automatic `__repr__`, `__eq__` for debugging
- Works well with strict mypy
- JSON serialization via `dataclasses.asdict()`

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
- Excellent support for command groups
- Decorator-based for clean code
- Good testing utilities

**Extension:** Custom `LintroGroup` class adds command chaining and aliases.

## Future Architecture Considerations

### Tool Definitions as Data

**Current:** Each tool is a Python class with embedded configuration.

**Future:** Tool definitions as YAML/TOML files with generic execution.

```yaml
# tools/definitions/python/ruff.yaml
name: ruff
type: linter
languages: [python]
file_patterns: ['*.py', '*.pyi']
check_command: ['ruff', 'check', '--output-format=json']
fix_command: ['ruff', 'check', '--fix', '--output-format=json']
parser: json_lines
field_mapping:
  code: code
  message: message
  file: filename
  line: location.row
```

**Benefits:**

- Adding a tool = adding a YAML file (no Python code)
- Non-developers can contribute tool definitions
- Easier to maintain and validate

### Generic Parser Factory

**Current:** 15+ individual parser implementations.

**Future:** 3-4 generic parser types.

```python
class ParserFactory:
    @staticmethod
    def create(config: ParserConfig) -> Parser:
        match config.format:
            case "json_lines":
                return JsonLinesParser(config.field_mapping)
            case "json_array":
                return JsonArrayParser(config.field_mapping)
            case "regex":
                return RegexParser(config.pattern, config.groups)
```

### Parallel Tool Execution

**Current:** Tools run sequentially.

**Future:** Independent tools run in parallel.

```python
# Future implementation concept
async def execute_tools(tools: list[Tool], paths: list[str]) -> list[ToolResult]:
    independent_groups = group_by_conflicts(tools)
    results = []
    for group in independent_groups:
        group_results = await asyncio.gather(
            *[tool.check(paths) for tool in group]
        )
        results.extend(group_results)
    return results
```

### Distribution Strategy

**Current:** Python package via PyPI, requires Python runtime.

**Future Options (in order of preference):**

#### Option 1: Nuitka Compilation

Compile Python to optimized C, produce standalone binary.

```bash
nuitka --standalone --onefile lintro/__main__.py
```

**Pros:** Pure Python toolchain, no new language **Cons:** Large binary (~50-100MB),
some edge cases

#### Option 2: PyOxidizer

Rust-based Python packager.

```bash
pyoxidizer build
```

**Pros:** Smaller binaries, better startup time **Cons:** Rust dependency in build
process

#### Option 3: Rust CLI Wrapper (Long-term)

Thin Rust binary that embeds Python via PyO3.

```text
┌─────────────────────────────────────────┐
│  Rust Binary (thin wrapper)             │
│  - Fast CLI parsing                     │
│  - Parallel tool orchestration          │
│  - File discovery                       │
└─────────────────┬───────────────────────┘
                  │ PyO3
                  ▼
┌─────────────────────────────────────────┐
│  Embedded Python                        │
│  - Tool definitions                     │
│  - Output parsing                       │
│  - Configuration                        │
└─────────────────────────────────────────┘
```

**Pros:** Maximum performance, single binary **Cons:** Rust expertise required, build
complexity

#### Option 4: Full Rust Rewrite (Very Long-term)

Complete rewrite in Rust, similar to Ruff or Oxc.

**Pros:** Maximum performance, smallest binary **Cons:** Major undertaking, loses Python
ecosystem benefits

**Recommendation:** Start with Option 1 (Nuitka), evaluate Option 3 if performance
becomes critical.

## Directory Structure

### Current Structure

```text
lintro/
├── __init__.py
├── __main__.py
├── cli.py                    # Main CLI entry point
├── cli_utils/
│   └── commands/            # CLI command implementations
├── config/                  # Configuration management
├── enums/                   # Type-safe enumerations
├── exceptions/              # Custom exception hierarchy
├── formatters/              # Output formatting
│   ├── core/
│   └── styles/
├── models/                  # Data models (ToolResult, etc.)
│   └── core/
├── parsers/                 # Tool output parsers
│   ├── ruff/
│   ├── black/
│   └── ...
├── plugins/                 # Plugin system
└── tools/
    ├── core/               # Tool management
    └── implementations/    # Concrete tool classes
```

### Target Structure (Future)

```text
lintro/
├── cli/                     # CLI layer (thin)
│   ├── commands/
│   └── output/
├── core/                    # Orchestration
│   ├── executor.py
│   ├── parallel.py
│   └── discovery.py
├── plugins/                 # Plugin system (unchanged)
├── parsers/                 # Generic parsers only
│   ├── json_parser.py
│   ├── regex_parser.py
│   └── factory.py
├── formatters/              # Output formatters
├── config/                  # Configuration
├── models/                  # Data models
└── tools/
    ├── registry.py
    ├── executor.py
    └── definitions/         # YAML/TOML per tool
        ├── python/
        │   ├── ruff.yaml
        │   └── black.yaml
        ├── javascript/
        │   └── prettier.yaml
        └── ...
```

## Testing Strategy

### Test Organization

```text
tests/
├── unit/                    # Isolated unit tests
│   ├── parsers/            # Parser tests (one per parser type)
│   ├── formatters/         # Formatter tests
│   ├── config/             # Configuration tests
│   └── plugins/            # Plugin system tests
├── integration/             # End-to-end tests
│   └── tools/              # Full tool workflow tests
└── fixtures/                # Shared test data
```

### Coverage Targets

| Component           | Current | Target  |
| ------------------- | ------- | ------- |
| Core (cli, plugins) | 45%     | 80%     |
| Parsers             | 60%     | 90%     |
| Formatters          | 70%     | 90%     |
| Tools               | 50%     | 75%     |
| **Overall**         | **47%** | **70%** |

### Test Patterns

```python
# Unit test - isolated, fast, mocked dependencies
def test_json_parser_extracts_fields():
    output = '{"code": "E501", "message": "line too long"}'
    parser = JsonLinesParser(field_mapping={"code": "code"})
    issues = parser.parse(output)
    assert issues[0].code == "E501"

# Integration test - real tool execution
def test_ruff_check_finds_issues(tmp_path):
    (tmp_path / "bad.py").write_text("x=1")  # Missing spaces
    result = RuffPlugin().check([str(tmp_path)])
    assert result.issues_count > 0
```

## Performance Considerations

### Current Bottlenecks

1. **Sequential tool execution** - Tools run one after another
2. **Subprocess overhead** - Each tool spawns a new process
3. **Full file discovery** - Scans all files even if unchanged

### Optimization Roadmap

1. **Parallel execution** - Run independent tools concurrently
2. **Incremental checking** - Hash files, skip unchanged
3. **File discovery caching** - Cache glob results within session
4. **Subprocess pooling** - Reuse processes for same tool

### Performance Metrics (To Establish)

| Metric            | Measurement Method           |
| ----------------- | ---------------------------- |
| Startup time      | Time to first output         |
| Per-tool overhead | Time for tool with 0 files   |
| Scaling factor    | Time increase per 1000 files |
| Memory usage      | Peak RSS during execution    |

## Security Considerations

### Current Measures

- `defusedxml` for XML parsing (prevents XXE attacks)
- `bandit` integration for Python security scanning
- CodeQL analysis in CI
- SBOM generation for transparency
- Path validation before subprocess execution

### Subprocess Security

```python
# GOOD: Validated paths, no shell=True
subprocess.run(
    [tool_path, "--check", *validated_paths],
    capture_output=True,
    timeout=timeout,
    cwd=working_directory,
)

# BAD: Never do this
subprocess.run(
    f"{tool} {user_input}",  # Command injection risk!
    shell=True,
)
```

## Related Documents

- [VISION.md](./VISION.md) - Project vision and guiding principles
- [ROADMAP.md](./ROADMAP.md) - Prioritized improvements
- [../style-guide.md](../style-guide.md) - Coding standards
- [../contributing.md](../contributing.md) - Contribution guidelines
