# Oxc Tools Analysis (oxlint + oxfmt)

## Overview

The Oxc (Oxidation Compiler) project provides extremely fast JavaScript/TypeScript
tooling written in Rust. Lintro integrates two Oxc tools:

- **oxlint**: Linter (50-100x faster than ESLint, 661+ built-in rules)
- **oxfmt**: Formatter (30x faster than Prettier)

This analysis compares Lintro's wrapper implementations with the core tools.

---

## Oxlint (Linter)

### Core Capabilities

- **Linting**: 661+ rules covering ESLint, TypeScript, React, JSX-a11y, Unicorn, and
  more
- **Performance**: Extremely fast execution (50-100x faster than ESLint)
- **Auto-fixing**: Many rules support automatic fixes via `--fix`
- **JSON output**: Machine-readable output with `--format json`
- **Configuration**: `.oxlintrc.json` or command-line options
- **TypeScript**: Native TypeScript support without additional configuration

### Lintro Implementation

**Preserved Features:**

- Linting via `oxlint --format json`
- Auto-fixing via `oxlint --fix`
- Configuration file support (`.oxlintrc.json`)
- File patterns: `*.js`, `*.ts`, `*.jsx`, `*.tsx`, `*.vue`, `*.svelte`, `*.astro`

**Configuration Options:**

- `exclude_patterns`: List of patterns to exclude
- `quiet`: Suppress warnings, only report errors
- `timeout`: Configurable execution timeout
- `verbose_fix_output`: Include raw output in fix results
- `config`: Path to Oxlint config file (--config)
- `tsconfig`: Path to tsconfig.json for TypeScript support (--tsconfig)
- `type_aware`: Enable type-aware linting (--type-aware); see
  [Type-aware linting](#type-aware-linting-oxlint)
- `allow`: Rules to allow/turn off (--allow)
- `deny`: Rules to deny/report as errors (--deny)
- `warn`: Rules to warn on (--warn)

**Limited/Missing Features:**

- No plugin enabling flags (`--react-perf-plugin`, `--nextjs-plugin`)
- No watch mode (`--watch`)
- No cache control

### Usage Comparison

```bash
# Core oxlint
oxlint src/
oxlint --deny no-debugger --allow no-console src/
oxlint --fix src/
```

```python
# Lintro wrapper
oxlint_plugin = get_plugin("oxlint")
result = oxlint_plugin.check(["src/"])
result = oxlint_plugin.fix(["src/"])
oxlint_plugin.set_options(quiet=True, exclude_patterns=["node_modules"])
```

### Rule Categories

| Category                | Description                   |
| ----------------------- | ----------------------------- |
| **ESLint**              | Core JavaScript linting rules |
| **typescript-eslint**   | TypeScript-specific rules     |
| **eslint-plugin-react** | React best practices          |
| **jsx-a11y**            | Accessibility rules for JSX   |
| **unicorn**             | Various helpful rules         |
| **import**              | Import/export validation      |

### Type-aware linting (Oxlint)

Oxlint supports **type-aware** linting via `--type-aware`. Lintro exposes this as
the boolean `type_aware` option, which appends `--type-aware` to the underlying
command:

```python
oxlint_plugin = get_plugin("oxlint")
oxlint_plugin.set_options(type_aware=True)
result = oxlint_plugin.check(["src/"])
```

**Requirements:**

- **`oxlint-tsgolint`**: the type-aware companion binary must be resolvable, either
  from the project's `node_modules` or via `bunx`. Install it with
  `bun add -d oxlint-tsgolint@latest`.
- **TypeScript >= 7.0**: type-aware rules rely on the TypeScript-Go (`tsgolint`)
  toolchain and require TypeScript 7.0 or newer.
- **No legacy `tsconfig` `baseUrl`**: projects that depend on the legacy
  `compilerOptions.baseUrl` path resolution are not supported by the type-aware
  backend; migrate to `paths`/relative imports first.

**Status:** type-aware linting is currently **alpha** in oxlint. Rules that require
type information live under the `typescript/*` rule namespace.

`lintro doctor` verifies these prerequisites automatically whenever type-aware
linting is enabled — either through the `type_aware` option or via
`options.typeAware` in the discovered `.oxlintrc.json`. When `oxlint-tsgolint`
cannot be resolved it emits the hint `bun add -d oxlint-tsgolint@latest`, and it
flags TypeScript installs older than 7.0 as incompatible.

Example `.oxlintrc.json` enabling type-aware linting:

```json
{
  "options": {
    "typeAware": true
  },
  "rules": {
    "typescript/no-floating-promises": "error"
  }
}
```

### JavaScript plugins (Oxlint)

Oxlint can load **ESLint v9+ JavaScript plugins** declared in `.oxlintrc.json`.
These plugins pass through Lintro unchanged: Lintro does not intercept, rewrite, or
re-declare plugin configuration, so any plugins listed in your `.oxlintrc.json`
`plugins`/`jsPlugins` arrays are honored exactly as oxlint resolves them. This is a
documentation-only guarantee — there is no Lintro-specific code path for JS plugins;
configure them natively:

```json
{
  "plugins": ["react", "unicorn"],
  "jsPlugins": ["eslint-plugin-example"]
}
```

---

## Oxfmt (Formatter)

### Oxfmt Core Capabilities

- **Formatting**: JS, TS, JSX, TSX, Vue
- **Performance**: Approximately 30x faster than Prettier
- **Check mode**: Verify formatting via `--check --list-different`
- **Write mode**: Format in place via `--write`
- **Prettier compatibility**: Aims for Prettier-compatible output

> **Note**: Unlike Prettier, oxfmt currently only supports JavaScript/TypeScript and Vue
> files. It does not support Svelte, Astro, JSON, CSS, HTML, or Markdown.

### Oxfmt Lintro Implementation

**Preserved Features:**

- Check mode via `oxfmt --check --list-different`
- Fix mode via `oxfmt --write`
- Configuration file support (`.oxfmtrc.json`, `.oxfmtrc.jsonc`)
- Extensive file patterns for all supported types

**Configuration Options:**

- `timeout`: Configurable execution timeout
- `verbose_fix_output`: Include raw output in fix results
- `config`: Path to oxfmt config file (--config)
- `ignore_path`: Path to ignore file (--ignore-path)

> **Note:** Formatting options (`printWidth`, `tabWidth`, `useTabs`, `semi`,
> `singleQuote`) are only supported via config file (`.oxfmtrc.json`), not CLI flags.

**Limited/Missing Features:**

- No stdin/stdout piping support
- No explicit parser selection

### Oxfmt Usage Comparison

```bash
# Core oxfmt
oxfmt --check src/
oxfmt --write src/
```

```python
# Lintro wrapper
oxfmt_plugin = get_plugin("oxfmt")
result = oxfmt_plugin.check(["src/"])
result = oxfmt_plugin.fix(["src/"])
```

### Supported File Types

| Category       | Extensions                    |
| -------------- | ----------------------------- |
| **JavaScript** | `.js`, `.mjs`, `.cjs`, `.jsx` |
| **TypeScript** | `.ts`, `.mts`, `.cts`, `.tsx` |
| **Frameworks** | `.vue`                        |

---

## Configuration

### Oxlint Configuration

Example `.oxlintrc.json`:

```json
{
  "rules": {
    "no-debugger": "error",
    "no-console": "warn",
    "eqeqeq": "error"
  },
  "plugins": ["react", "unicorn"],
  "ignorePatterns": ["dist/**", "node_modules/**"]
}
```

### Oxfmt Configuration

Example `.oxfmtrc.json`:

```json
{
  "printWidth": 100,
  "tabWidth": 2,
  "useTabs": false,
  "semi": true,
  "singleQuote": true,
  "trailingComma": "es5"
}
```

---

## Recommendations

### When to Use Core Tools Directly

- Need maximum configuration flexibility
- Require specific plugins or formatting options
- Want watch mode for development
- Need stdin/stdout piping

### When to Use Lintro Wrapper

- Part of multi-tool linting/formatting pipeline
- Need consistent issue reporting across tools
- Want Python object integration
- Require standardized error handling and timeout protection

---

## Migration Guide

### From ESLint to Oxlint

1. Install: `npm install -g oxlint` or `bun add -g oxlint`
2. Run oxlint alongside ESLint to compare results
3. Create `.oxlintrc.json` for custom rule configuration
4. Update CI/CD (keep ESLint as fallback initially)
5. Remove ESLint once confident

**Rule Mapping** (most rules have identical names):

| ESLint Rule      | Oxlint Rule      |
| ---------------- | ---------------- |
| `no-debugger`    | `no-debugger`    |
| `no-console`     | `no-console`     |
| `eqeqeq`         | `eqeqeq`         |
| `no-unused-vars` | `no-unused-vars` |

### From Prettier to Oxfmt

1. Install: `npm install -g oxfmt` or `bun add -g oxfmt`
2. Run oxfmt alongside Prettier to compare output
3. Create `.oxfmtrc.json` matching your `.prettierrc`
4. Update CI/CD and editor configurations

**Configuration Mapping**:

| Prettier Option  | Oxfmt Option     |
| ---------------- | ---------------- |
| `printWidth`     | `printWidth`     |
| `tabWidth`       | `tabWidth`       |
| `useTabs`        | `useTabs`        |
| `semi`           | `semi`           |
| `singleQuote`    | `singleQuote`    |
| `trailingComma`  | `trailingComma`  |
| `bracketSpacing` | `bracketSpacing` |

---

## Out of Scope for Lintro

The Oxc project includes additional components that are not integrated into Lintro:

| Component       | Purpose             | Reason for Exclusion             |
| --------------- | ------------------- | -------------------------------- |
| **Parser**      | AST parsing library | Internal library, not a CLI tool |
| **Transformer** | Code transpilation  | Build tool, not lint/format      |
| **Resolver**    | Module resolution   | Internal library, not a CLI tool |
| **Minifier**    | Code minification   | Build tool, not lint/format      |

These components are intended for use by other tools and build systems, not for direct
invocation in a linting/formatting workflow.

---

## Limitations and Workarounds

### Oxlint Limitations

| Limitation               | Workaround                            |
| ------------------------ | ------------------------------------- |
| No plugin enabling flags | Configure plugins in `.oxlintrc.json` |
| No watch mode via Lintro | Use `oxlint --watch` directly         |
| No cache control         | Use native oxlint cache options       |

### Oxfmt Limitations

| Limitation                  | Workaround                      |
| --------------------------- | ------------------------------- |
| No stdin support via Lintro | Use `oxfmt` directly for piping |
| No explicit parser override | Use appropriate file extensions |

---

## Complete Feature Comparison Matrix

### Oxlint: Native CLI vs Lintro Support

| Native CLI Flag     | Lintro Support | Rationale                                             |
| ------------------- | -------------- | ----------------------------------------------------- |
| `--format json`     | ✅ Used        | Required for parsing structured output                |
| `--config <path>`   | ✅ Supported   | Essential for project configuration                   |
| `--tsconfig <path>` | ✅ Supported   | Required for TypeScript projects                      |
| `--allow <rule>`    | ✅ Supported   | Essential for rule customization                      |
| `--deny <rule>`     | ✅ Supported   | Essential for rule customization                      |
| `--warn <rule>`     | ✅ Supported   | Essential for rule customization                      |
| `--fix`             | ✅ Supported   | Core auto-fix functionality                           |
| `--quiet`           | ✅ Supported   | Useful for CI/CD pipelines                            |
| `--init`            | ❌ Not exposed | One-time setup tool, use `oxlint --init` directly     |
| `--react-plugin`    | ❌ Not exposed | Configure in `.oxlintrc.json` plugins array           |
| `--jest-plugin`     | ❌ Not exposed | Configure in `.oxlintrc.json` plugins array           |
| `--nextjs-plugin`   | ❌ Not exposed | Configure in `.oxlintrc.json` plugins array           |
| `--jsx-a11y-plugin` | ❌ Not exposed | Configure in `.oxlintrc.json` plugins array           |
| `--fix-suggestions` | ❌ Not exposed | Safety: only standard fixes via `--fix`               |
| `--fix-dangerously` | ❌ Not exposed | Safety: dangerous fixes not recommended in automation |
| `--ignore-pattern`  | ❌ Not exposed | Use `.oxlintrc.json` ignorePatterns instead           |
| `--no-ignore`       | ❌ Not exposed | Rarely needed in automated workflows                  |
| `--max-warnings`    | ❌ Not exposed | Use CI exit codes instead                             |
| `--print-config`    | ❌ Not exposed | Debugging tool, use `oxlint --print-config` directly  |
| `--threads`         | ❌ Not exposed | Auto-tuned, rarely needs manual control               |
| `--type-aware`      | ✅ Supported   | Type-aware linting via the `type_aware` option        |
| `--lsp`             | ❌ Not exposed | LSP mode not applicable to CLI wrapper                |
| `--watch`           | ❌ Not exposed | Development workflow, not batch processing            |
| Non-JSON formats    | ❌ Not exposed | Lintro normalizes all output to structured format     |

### Oxfmt: Native CLI vs Lintro Support

| Native CLI Flag                   | Lintro Support | Rationale                                            |
| --------------------------------- | -------------- | ---------------------------------------------------- |
| `--check`                         | ✅ Used        | Core check functionality (via --list-different)      |
| `--list-different`                | ✅ Used        | Required to identify files needing formatting        |
| `--write`                         | ✅ Used        | Core fix functionality                               |
| `--config <path>`                 | ✅ Supported   | Essential for project configuration                  |
| `--ignore-path <path>`            | ✅ Supported   | Essential for ignore patterns                        |
| `--init`                          | ❌ Not exposed | One-time setup tool, use `oxfmt --init` directly     |
| `--migrate=<source>`              | ❌ Not exposed | One-time migration, use `oxfmt --migrate` directly   |
| `--stdin-filepath`                | ❌ Not exposed | Breaks file-based abstraction                        |
| `--with-node-modules`             | ❌ Not exposed | Rarely needed, security risk                         |
| `--no-error-on-unmatched-pattern` | ❌ Not exposed | Lintro handles pattern matching internally           |
| `--threads`                       | ❌ Not exposed | Auto-tuned, rarely needs manual control              |
| `--lsp`                           | ❌ Not exposed | LSP mode not applicable to CLI wrapper               |
| Formatting options (CLI)          | ❌ N/A         | Intentional: oxfmt only supports config file options |

> **Design Note:** Oxfmt intentionally does not support formatting options via CLI
> flags. This ensures consistent settings across CLI and editor integrations. Lintro
> follows this design philosophy and requires `.oxfmtrc.json` for formatting
> configuration.

---

## Feature Exclusion Rationale

### Plugin Control (Oxlint)

**Why excluded:** Plugin enabling/disabling is best done via configuration files to
ensure reproducible builds. Runtime plugin control adds complexity and can lead to
inconsistent results between local and CI environments.

**Workaround:** Configure plugins in `.oxlintrc.json`:

```json
{
  "plugins": ["react", "jsx-a11y", "nextjs"]
}
```

### Dangerous/Suggestion Fixes (Oxlint)

**Why excluded:** Automated tooling should be conservative. Dangerous fixes may alter
code semantics, and suggestion fixes may not always be appropriate. Manual review is
recommended for these fix types.

**Workaround:** Run `oxlint --fix-dangerously` or `oxlint --fix-suggestions` directly
when you need these capabilities and can review the changes.

### Stdin/Stdout Piping (Oxfmt)

**Why excluded:** Lintro uses a file-based abstraction that discovers files, filters by
patterns, and processes results. Stdin piping doesn't fit this model and would require a
different API.

**Workaround:** Use `oxfmt` directly for piping:

```bash
echo 'const x=1' | oxfmt --stdin-filepath test.js
```

### Watch Mode (Both Tools)

**Why excluded:** Watch mode is a development workflow feature. Lintro is designed for
batch processing in CI/CD and pre-commit hooks, not continuous development monitoring.

**Workaround:** Run the native tools with watch mode:

```bash
oxlint --watch src/
```

---

## Future Enhancement Opportunities

### Oxlint Enhancements

1. Plugin enable/disable flags at runtime (`--react-perf-plugin`, etc.)
2. Watch mode integration
3. Cache control options

### Oxfmt Enhancements

1. Stdin/stdout support for piping
2. Diff output mode
3. Explicit parser selection
