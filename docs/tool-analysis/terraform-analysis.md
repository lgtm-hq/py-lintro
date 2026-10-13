# Terraform Tool Analysis

## Overview

Terraform is HashiCorp's infrastructure-as-code tool. Lintro wraps two of its
subcommands: `terraform fmt`, a formatter that rewrites `*.tf`/`*.tfvars` files to the
canonical style, and `terraform validate`, a check-only validator that verifies whether
a module's configuration is internally consistent. This analysis compares Lintro's
wrapper implementation with the core Terraform CLI.

## Core Tool Capabilities

Terraform provides the following relevant capabilities:

- **Formatting**: `terraform fmt` rewrites configuration files to a canonical layout;
  `-check` reports which files are not formatted without modifying them.
- **Validation**: `terraform validate` checks that a configuration is syntactically
  valid and internally consistent (declared variables, references, types), independent
  of any provider APIs or remote state.
- **Structured output**: `terraform validate -json` emits machine-readable diagnostics
  with severity, summary, detail, and a source range.
- **Recursive operation**: `terraform fmt -recursive` walks directories.

## Lintro Implementation Analysis

### ✅ Preserved Features

**Core Functionality:**

- ✅ **Formatting**: Runs `terraform fmt -check` for `check` and applies `terraform fmt`
  for `format`.
- ✅ **Validation**: Runs `terraform init -backend=false -input=false` followed by
  `terraform validate -json` per module directory (directories containing `.tf` files),
  skipping the `.terraform` provider cache.
- ✅ **File targeting**: Supports `*.tf` and `*.tfvars` patterns.
- ✅ **Diagnostic mapping**: Maps JSON diagnostics (severity, range, summary, detail) to
  standard Lintro issues.
- ✅ **Auto-fixing**: Formatting issues are fixable via `format`.

**Command Execution:**

```python
# Check
cmd = ["terraform", "fmt", "-check", *rel_files]
# Validate (per module directory)
["terraform", "init", "-backend=false", "-input=false", "-no-color"]
["terraform", "validate", "-json", "-no-color"]
# Fix
cmd = ["terraform", "fmt", *rel_files]
```

**Configuration Options:**

- ✅ **validate**: Toggle `terraform validate` on/off (default `true`). When disabled,
  Lintro only checks/fixes formatting and skips the `terraform init` step.

### ⚠️ Limited/Missing Features

- ⚠️ **Provider-dependent checks**: `validate` runs with `-backend=false` and does not
  contact providers or remote state, so it does not catch provider-specific errors that
  only surface during `plan`/`apply`.
- ❌ **`terraform plan`**: Lintro does not run `plan`; it is not a static check.
- ❌ **Workspace/variable injection**: No runtime injection of variables or workspaces.
- ❌ **Custom validation rules**: No support for policy-as-code (e.g. Sentinel, OPA).

### 🚀 Enhancements

**Unified Interface:**

- ✅ **Consistent API**: Same interface as other Lintro tools (`check()`, `fix()`,
  `set_options()`).
- ✅ **Structured output**: Formatting and validation issues normalized to standard
  `TerraformIssue` objects with a documentation URL.
- ✅ **Pipeline integration**: Runs alongside other tools with aggregated results.

**Enhanced Error Processing:**

- ✅ **fmt parsing**: Each offending file path from `terraform fmt -check` stdout
  becomes a formatting issue.
- ✅ **validate parsing**: JSON diagnostics are mapped to issues, preserving severity,
  line/column, summary, and detail.

## Usage Comparison

### Core Terraform

```bash
# Check formatting without modifying files
terraform fmt -check -recursive

# Apply formatting
terraform fmt -recursive

# Validate a module (after init)
terraform init -backend=false -input=false
terraform validate -json
```

### Lintro Wrapper

```bash
# Check formatting and validate configuration
lintro check --tools terraform

# Format Terraform files in place
lintro format --tools terraform

# Only check formatting (skip terraform init + validate)
lintro check --tools terraform --tool-options terraform:validate=False
```

## Configuration Strategy

Terraform respects its own native configuration and provider requirements. Lintro does
not inject configuration; it only toggles whether validation runs via the `validate`
option.

## Issue Code Mapping

| Code       | Origin                     | Description                               |
| ---------- | -------------------------- | ----------------------------------------- |
| `fmt`      | `terraform fmt -check`     | File is not correctly formatted           |
| `validate` | `terraform validate -json` | Configuration validation diagnostic       |
| `init`     | `terraform init`           | Initialization failed for a module        |
| `timeout`  | Lintro                     | A Terraform invocation exceeded the limit |

## Recommendations

### When to Use Core Terraform

- Need `terraform plan`/`apply` or provider-backed checks.
- Require workspace- or variable-specific validation.
- Want policy-as-code enforcement.

### When to Use Lintro Wrapper

- Part of a multi-tool linting pipeline.
- Need consistent issue reporting and aggregated results.
- Want formatting auto-fix plus lightweight validation in one pass.

## Limitations and Workarounds

### Provider-dependent validation

**Problem**: `validate` runs without backends/providers and cannot catch provider-level
errors. **Workaround**: Run `terraform plan` in a dedicated pipeline stage when needed.

### Initialization cost

**Problem**: `validate` requires `terraform init` per module directory. **Workaround**:
Disable validation with `terraform:validate=False` to run formatting-only checks
quickly.

## Future Enhancement Opportunities

1. **Recursive fmt targeting**: Optional `-recursive` directory mode.
2. **Variable files**: Support passing `-var-file` for richer validation.
3. **Policy integration**: Optional hooks for policy-as-code tools.
4. **Caching**: Reuse initialized provider caches across runs.
