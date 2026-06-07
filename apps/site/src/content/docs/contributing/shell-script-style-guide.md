---
title: 'shell scripts'
description:
  'This guide establishes standards for shell scripts in the lintro project to ensure
  consistency, reliability, and maintainability.'
category: contributing
order: 40
navGroup: standards
---

# Shell Script Style Guide

This guide establishes standards for shell scripts in the lintro project to ensure
consistency, reliability, and maintainability.

## Standard Preamble

Every shell script must start with this preamble:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/utils.sh"  # Adjust path as needed
```

### Explanation

- `#!/usr/bin/env bash`: Portable shebang that finds bash in PATH
- `set -e`: Exit immediately if a command fails
- `set -u`: Treat unset variables as errors
- `set -o pipefail`: Pipeline fails if any command fails
- `SCRIPT_DIR`: Reliable way to get the script's directory
- `source utils.sh`: Import shared logging and utility functions

## Help Message Pattern

All scripts should support `--help` and `-h` flags:

```bash
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Brief description of what the script does.

Usage: script-name.sh <required-arg> [optional-arg]

Arguments:
  required-arg    Description of required argument
  optional-arg    Description of optional argument (default: value)

Environment:
  SOME_VAR        Description of environment variable

Examples:
  script-name.sh foo
  script-name.sh foo bar
EOF
    exit 0
fi
```

Or use the `show_help` function from utils.sh:

```bash
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    show_help "script-name.sh" "Brief description" "<required-arg> [optional-arg]"
    exit 0
fi
```

## Logging Standards

Use the logging functions from `utils.sh`:

```bash
log_info "Starting process..."      # Blue info message
log_success "Operation completed"   # Green success message
log_warning "Something unexpected"  # Yellow warning message
log_error "Operation failed"        # Red error message
log_verbose "Debug details"         # Only shown when VERBOSE=1
```

### When to Use Each Level

| Level         | Use Case                       | Example                         |
| ------------- | ------------------------------ | ------------------------------- |
| `log_info`    | Progress updates, status       | "Processing 50 files..."        |
| `log_success` | Completed operations           | "Build completed successfully"  |
| `log_warning` | Non-fatal issues               | "File not found, using default" |
| `log_error`   | Failures (usually before exit) | "Required tool not installed"   |
| `log_verbose` | Debug info (VERBOSE=1)         | "Checking file: /path/to/file"  |

## Variable Handling

### Required Variables

Use parameter expansion with error messages:

```bash
REQUIRED_VAR="${1:?Usage: script.sh <required-arg>}"
```

### Optional Variables with Defaults

```bash
OPTIONAL_VAR="${2:-default_value}"
```

### Environment Variables

Document and provide defaults:

```bash
# Configuration (can be overridden via environment)
MAX_RETRIES="${MAX_RETRIES:-3}"
TIMEOUT="${TIMEOUT:-30}"
```

## Error Handling

### Check Command Success

```bash
if ! some_command; then
    log_error "some_command failed"
    exit 1
fi
```

### Cleanup on Exit

Use traps for cleanup:

```bash
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

TEMP_DIR=$(mktemp -d)
```

Or use the `create_temp_dir` function:

```bash
TEMP_DIR=$(create_temp_dir)  # Auto-cleanup on exit
```

### Validate Prerequisites

Check for required tools early:

```bash
for cmd in git curl jq; do
    if ! command -v "$cmd" &> /dev/null; then
        log_error "Required command not found: $cmd"
        exit 1
    fi
done
```

## GitHub Actions Integration

### Setting Outputs

Use the helper function:

```bash
set_github_output "key" "value"
```

Or directly:

```bash
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "key=value" >> "$GITHUB_OUTPUT"
fi
```

### Setting Environment Variables

```bash
set_github_env "MY_VAR" "my_value"
```

### Multiline Values

```bash
{
    echo "key<<EOF"
    echo "line 1"
    echo "line 2"
    echo "EOF"
} >> "$GITHUB_OUTPUT"
```

### Git Configuration for CI

```bash
configure_git_ci_user  # Sets github-actions[bot] identity
```

## File Operations

### Check File Existence

```bash
if [[ -f "$file" ]]; then
    log_info "Processing $file"
else
    log_warning "File not found: $file"
fi
```

Or use the helper:

```bash
check_file_exists "$file" "Configuration file"
```

### Safe File Writing

```bash
# Write to temp file first, then move
tmp_file=$(mktemp)
echo "content" > "$tmp_file"
mv "$tmp_file" "$target_file"
```

## Quoting Rules

1. **Always quote variables**: `"$var"` not `$var`
2. **Quote command substitutions**: `"$(command)"`
3. **Arrays need special handling**: `"${array[@]}"`

```bash
# Good
file_path="$HOME/documents/my file.txt"
result="$(some_command "$file_path")"

# Bad
file_path=$HOME/documents/my file.txt
result=$(some_command $file_path)
```

## Conditionals

### String Comparisons

```bash
if [[ "$var" == "value" ]]; then
    # ...
fi
```

### Numeric Comparisons

```bash
if [[ "$count" -gt 10 ]]; then
    # ...
fi
```

### File Tests

```bash
[[ -f "$file" ]]  # File exists and is regular file
[[ -d "$dir" ]]   # Directory exists
[[ -x "$cmd" ]]   # File is executable
[[ -n "$var" ]]   # Variable is non-empty
[[ -z "$var" ]]   # Variable is empty
```

## Loops

### Iterate Over Files

```bash
for file in *.txt; do
    [[ -f "$file" ]] || continue  # Skip if no matches
    process "$file"
done
```

### Read Lines from File

```bash
while IFS= read -r line; do
    process "$line"
done < "$file"
```

## Functions

### Definition

```bash
# Brief description of function
# Arguments:
#   $1 - description
#   $2 - description (optional, default: value)
# Returns:
#   0 on success, 1 on failure
my_function() {
    local arg1="$1"
    local arg2="${2:-default}"

    # Function body
}
```

### Local Variables

Always use `local` for function variables:

```bash
my_function() {
    local result
    result=$(some_command)
    echo "$result"
}
```

## Script Organization

```bash
#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Script Name: my-script.sh
# Description: Brief description
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/utils.sh"

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
readonly VERSION="1.0.0"
readonly DEFAULT_TIMEOUT=30

# ------------------------------------------------------------------------------
# Functions
# ------------------------------------------------------------------------------
show_usage() {
    # ...
}

main() {
    # Parse arguments
    # Validate prerequisites
    # Execute main logic
}

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    show_usage
    exit 0
fi

main "$@"
```

## Common Patterns

### Retry Logic

```bash
retry() {
    local max_attempts="${1:-3}"
    local delay="${2:-5}"
    shift 2
    local attempt=1

    while [[ $attempt -le $max_attempts ]]; do
        if "$@"; then
            return 0
        fi
        log_warning "Attempt $attempt/$max_attempts failed, retrying in ${delay}s..."
        sleep "$delay"
        ((attempt++))
    done

    log_error "All $max_attempts attempts failed"
    return 1
}

# Usage
retry 3 5 curl -sf "$url"
```

### Progress Indicator

```bash
total=${#files[@]}
current=0
for file in "${files[@]}"; do
    ((current++))
    log_info "Processing [$current/$total]: $file"
    process "$file"
done
```

## Linting

All scripts should pass shellcheck:

```bash
shellcheck scripts/**/*.sh
```

Common shellcheck directives when needed:

```bash
# shellcheck disable=SC2034  # Variable appears unused
# shellcheck source=scripts/utils/utils.sh
source "$SCRIPT_DIR/../utils/utils.sh"
```

## Testing

For complex scripts, consider adding tests:

```bash
# scripts/tests/test_my_script.sh
test_function_returns_expected() {
    result=$(my_function "input")
    [[ "$result" == "expected" ]] || {
        echo "FAIL: expected 'expected', got '$result'"
        return 1
    }
    echo "PASS: test_function_returns_expected"
}

test_function_returns_expected
```
