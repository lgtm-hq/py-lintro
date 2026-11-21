# TSC (TypeScript Compiler) Tool Analysis

## Overview

TSC is the TypeScript compiler that performs static type checking on TypeScript files.
This analysis compares Lintro's wrapper with core tsc behavior.

## Core Tool Capabilities

- Static type checking with full TypeScript type system
- Config discovery: `tsconfig.json` with `extends` chain support
- Build modes: `--build` for composite projects, `--watch` for development
- Output: JavaScript emission, declaration files, sourcemaps
- Flags: `--strict`, `--noEmit`, `--skipLibCheck`, `--project`, `--target`, `--module`,
  `--moduleResolution`, `--paths`, `--baseUrl`, and many more compiler options
- Incremental compilation with `--incremental` and `--tsBuildInfoFile`
- Project references for monorepo support

## Lintro Implementation Analysis

### ✅ Preserved Features

- ✅ Invokes tsc with `--noEmit --pretty false` for type checking without output
- ✅ Respects native `tsconfig.json` compiler options (auto-discovered or via
  `--project`)
- ✅ Conditional file targeting with `tsconfig.json`; explicit project scoping wins
- ✅ Respects tsconfig.json `include`/`exclude`/`files` scoping (#851)
- ✅ TypeScript project references (`references`) support (#803)
- ✅ Multi-project monorepo discovery and per-project type checking (#805)
- ✅ Per-project framework detection in monorepos
- ✅ Supports `--strict` mode toggle
- ✅ Supports `--skipLibCheck` for faster checks (enabled by default)
- ✅ File discovery for `*.ts`, `*.tsx`, `*.mts`, `*.cts`
- ✅ Intelligent command fallback: direct `tsc` -> `bunx tsc` -> `npx tsc`
- ✅ Parses tsc output into structured `ToolResult` with file/line/column/code

### File Targeting Behavior

Lintro chooses the checker configuration before constructing the native command:

1. An explicit `tsc:project`, or `tsc:use_project_files=True`, uses native project file
   selection.
2. An auto-discovered `tsconfig.json` with explicit `include`, `files` or `exclude`
   scoping also uses the native project configuration. This scoping can come through an
   `extends` chain. Supplying a smaller set of paths does not narrow that project.
3. Otherwise, a discovered config is extended by a temporary config targeting the
   selected files. With no config, file arguments are passed directly to the checker.

The temporary-config branch retains compiler options while narrowing file selection; it
is not a guarantee that every invocation checks only the supplied files.

```bash
# Supply files; a native config with explicit scoping can still select a wider project
lintro check src/utils.ts src/helpers.ts --tools tsc
# A temporary narrowed config is used only in the applicable branch described above

# Check all files defined in tsconfig.json (native behavior)
lintro check . --tools tsc --tool-options "tsc:use_project_files=True"
# → Uses tsconfig.json as-is, checks all files in include/files patterns
```

**Respecting tsconfig scoping (#851):** When your `tsconfig.json` has an explicit
`include`, `files`, or `exclude` field, lintro now respects it — running
`tsc --project <tsconfig>` directly instead of generating a temp config that overrides
the scoping. This prevents false positives from files the project intentionally excludes
(e.g., `vitest.config.ts`).

### Monorepo Support (#803, #805)

Lintro automatically discovers TypeScript sub-projects in monorepos:

1. **Project references:** Follows `references` arrays in tsconfig.json recursively
2. **Tree walking:** Discovers `tsconfig.json` files in subdirectories
3. **Deepest wins:** When parent and child tsconfigs overlap, files are assigned to the
   deepest (most specific) tsconfig, preventing double-checking under conflicting
   compiler options
4. **Per-project framework detection:** Astro/Vue/Svelte detection is scoped per
   sub-project, not globally

```bash
# Monorepo with project references — each sub-project checked independently
lintro check . --tools tsc
# → packages/api: tsc -p packages/api/tsconfig.json
# → packages/web: skipped (Vue detected, use vue-tsc)
# → packages/lib: tsc -p packages/lib/tsconfig.json
```

### ⚠️ Limited / Missing

**Build & Watch Modes:**

- ❌ No native `--watch` pass-through; use `lintro watch` for repeated Lintro checks
- ❌ No `--build` mode (composite project building)
- ❌ No `--incremental` caching (each run is fresh)

**Output Generation:**

- ❌ No JavaScript emission (always uses `--noEmit`)
- ❌ No declaration file generation (`--declaration`, `--declarationMap`)
- ❌ No sourcemap generation (`--sourceMap`, `--inlineSourceMap`)
- ❌ No output directory control (`--outDir`, `--outFile`)

**Compiler Options (config-file-only):**

- ⚠️ `target`, `module`, `moduleResolution` - must be set in tsconfig.json
- ⚠️ `paths`, `baseUrl`, `rootDir`, `rootDirs` - must be set in tsconfig.json
- ⚠️ `lib`, `types`, `typeRoots` - must be set in tsconfig.json
- ⚠️ `esModuleInterop`, `allowSyntheticDefaultImports` - must be set in tsconfig.json
- ⚠️ `jsx`, `jsxFactory`, `jsxFragmentFactory` - must be set in tsconfig.json
- ⚠️ `experimentalDecorators`, `emitDecoratorMetadata` - must be set in tsconfig.json
- ⚠️ All other `compilerOptions` not exposed via `--tool-options`

**Advanced Features:**

- ❌ No plugins configuration
- ❌ No `--generateTrace` performance profiling
- ❌ No custom diagnostic formatting
- ❌ No `--listFiles`, `--listEmittedFiles` introspection

### 🚀 Enhancements

- ✅ Safe timeout handling (default 60s) with structured timeout result
- ✅ Auto config discovery prioritizes `tsconfig.json` in working directory
- ✅ Temporary-config targeting when native project scoping does not take precedence
- ✅ Normalized `ToolResult` with parsed issues from `tsc_parser`
- ✅ Tool type `LINTER | TYPE_CHECKER`, same as mypy
- ✅ Windows path normalization in parser output
- ✅ Graceful handling when tsc is not installed with helpful install hints
- ✅ **Dependency error categorization** - separates missing module errors from type
  errors
- ✅ **Auto-install support** - optionally install Node.js deps before running tsc

## Dependency Error Handling

Lintro intelligently categorizes tsc errors to distinguish between actual type errors
and errors caused by missing dependencies (e.g., when `node_modules` is not installed).

### Error Categories

**Dependency errors** (TS2307, TS2688, TS7016):

- `TS2307`: Cannot find module 'X' or its corresponding type declarations
- `TS2688`: Cannot find type definition file for 'X'
- `TS7016`: Could not find a declaration file for module 'X'

**Type errors** (all other codes):

- Actual TypeScript type checking issues in your code

### Output Example

When dependency errors are detected, Lintro shows them separately with actionable
guidance:

```text
Results: tsc
├── Type errors: 3
│   └── src/utils.ts:15 - Type 'string' not assignable to 'number'
│   └── ...
└── Missing dependencies: 4
    └── vite/client, react, @types/node, ...

    Suggestions:
    - Run 'bun install' or 'npm install'
    - Use '--auto-install' flag
```

### Auto-Install Dependencies

Use the `--auto-install` flag to automatically install Node.js dependencies before
running tsc:

```bash
# Auto-install deps then run tsc
lintro check src/ --tools tsc --auto-install

# Enable globally in config
# .lintro-config.yaml
execution:
  auto_install_deps: true
```

**Package manager preference:**

1. `bun install --frozen-lockfile` (falls back to `bun install`)
2. `npm ci` (falls back to `npm install`)

**Note:** In Docker, dependencies are auto-installed by default when the container
starts.

## Usage Comparison

```bash
# Core tsc - type check only (checks all files in tsconfig.json)
tsc --noEmit

# Core tsc - with specific config
tsc --project tsconfig.app.json --noEmit

# Lintro wrapper - supply files; native project scoping can take precedence
lintro check src/utils.ts --tools tsc

# Lintro wrapper - check directory (finds all .ts/.tsx files)
lintro check src/ --tools tsc

# Lintro wrapper - use tsconfig.json file selection (native behavior)
lintro check . --tools tsc --tool-options "tsc:use_project_files=True"

# Lintro wrapper - enable strict mode override
lintro check src/ --tools tsc --tool-options "tsc:strict=True"

# Lintro wrapper - use specific config file
lintro check src/ --tools tsc --tool-options "tsc:project=tsconfig.build.json"
```

## Configuration Strategy

- **File selection:** Explicit projects and native config scoping take precedence; other
  applicable branches narrow to the supplied files
- **Compiler options inherited:** All settings from `tsconfig.json` are preserved
- **No config injection:** Lintro cannot modify tsconfig.json settings; tool is "Native
  only"
- **Tool options available:**
  - `tsc:project` (string) - path to tsconfig.json file
  - `tsc:strict` (bool) - enable `--strict` flag
  - `tsc:skip_lib_check` (bool) - enable `--skipLibCheck` (default: true)
  - `tsc:use_project_files` (bool) - request native project file selection (default:
    false); false does not override an explicit project or native config scoping
  - `tsc:timeout` (int) - execution timeout in seconds (default: 60)
- **Config display:** `lintro config -v` shows parsed tsconfig.json compilerOptions

## Priority and Conflicts

- **Ordering:** Derived from capability claims and tool-ordering configuration.
- **Tool Type:** LINTER | TYPE_CHECKER
- **Conflicts:** None
- **Complements:** oxlint, oxfmt, prettier (formatting/linting)

## Recommendations

- **Use Lintro** when you want quick type checking integrated into a multi-tool workflow
  with normalized output and timeout safety.
- **Use core tsc directly** when you need:
  - Watch mode for development (`tsc --watch`)
  - Build mode for composite projects (`tsc --build`)
  - Incremental compilation for large projects
  - JavaScript/declaration file output
  - Fine-grained compiler option control beyond tsconfig.json
