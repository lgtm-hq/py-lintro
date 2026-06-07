---
title: 'pydoclint'
description: ''
category: tools
order: 140
navGroup: python
---

# pydoclint Tool Analysis

## Overview

pydoclint is a Python docstring linter that validates docstrings match function
signatures. It checks for missing, extra, or incorrectly documented parameters, return
values, and raised exceptions.

## Recommended Configuration

The following configuration follows **Google Python Style Guide** best practices, which
state that type hints belong in function signatures, not duplicated in docstrings:

> "The description should include required type(s) **if the code does not contain a
> corresponding type annotation.**"

```toml
[tool.pydoclint]
style = "google"
arg-type-hints-in-docstring = false  # Types in annotations, not docstrings
arg-type-hints-in-signature = true   # Require type annotations in signatures
check-return-types = false           # Don't require return types in docstrings
check-arg-order = true               # Verify argument order matches signature
skip-checking-short-docstrings = true
```

### Why These Settings?

**Modern Python style** uses type annotations in function signatures for type checking
(mypy, pyright) while docstrings focus on **semantic descriptions** of what parameters
represent and how to use them.

**Without this configuration**, pydoclint defaults to requiring duplicated type
information:

```python
# pydoclint default expectation (REDUNDANT):
def process(path: str, timeout: int) -> bool:
    """Process a file.

    Args:
        path (str): File path.      # ← duplicated type
        timeout (int): Seconds.     # ← duplicated type

    Returns:
        bool: Success status.       # ← duplicated type
    """
```

**With recommended configuration** (types only in annotations):

```python
# Clean, non-redundant style:
def process(path: str, timeout: int) -> bool:
    """Process a file.

    Args:
        path: File path to process.
        timeout: Maximum seconds to wait.

    Returns:
        True if successful, False otherwise.
    """
```

This approach:

- Eliminates maintenance burden of keeping types synchronized
- Follows Google Python Style Guide
- Works with type checkers (mypy reads annotations, not docstrings)
- Keeps docstrings focused on **what** and **why**, not type information

## Installation

```bash
pip install pydoclint
```

Or with uv:

```bash
uv pip install pydoclint
```

## Output Format

pydoclint outputs issues with the file path on its own line, followed by indented issue
lines:

```text
path/file.py
    line: DOCxxx: message
```

Example output:

```text
src/module.py
    10: DOC101: Function `calculate` has 2 argument(s) in signature: ['a', 'b']. Arguments 1 to 2 are not documented.
    25: DOC201: Function `process` does not have a return section in docstring.
    40: DOC301: `__init__` has a docstring but the class doesn't.
```

## Common Error Codes

### Function/Method Arguments (DOC1xx)

| Code   | Description                                      |
| ------ | ------------------------------------------------ |
| DOC101 | Docstring has fewer arguments than signature     |
| DOC102 | Docstring has more arguments than signature      |
| DOC103 | Docstring arguments differ from signature        |
| DOC104 | Arguments in different order                     |
| DOC105 | Argument type hints don't match                  |
| DOC106 | Duplicate argument in docstring                  |
| DOC107 | No type hints in signature, not required in docs |
| DOC108 | Type hints in signature but not in docstring     |
| DOC109 | `--arg-type-hints-in-docstring` but none in docs |
| DOC110 | Not all args have type hints in docstring        |
| DOC111 | Missing `**kwargs` in docstring                  |

### Return Values (DOC2xx)

| Code   | Description                              |
| ------ | ---------------------------------------- |
| DOC201 | Missing return section in docstring      |
| DOC202 | Return section but no return in function |
| DOC203 | Return type mismatch                     |

### Class Docstrings (DOC3xx)

| Code   | Description                                       |
| ------ | ------------------------------------------------- |
| DOC301 | `__init__` has docstring but class doesn't        |
| DOC302 | Class and `__init__` both have docstring          |
| DOC303 | `__init__` should have args in its own docstring  |
| DOC304 | Class docstring has `Args` but not for `__init__` |
| DOC305 | Class docstring missing `Args` for `__init__`     |
| DOC306 | `__init__` `Args` don't belong in class docstring |

### Raises Documentation (DOC5xx)

| Code   | Description                                |
| ------ | ------------------------------------------ |
| DOC501 | Raises section but no raises in body       |
| DOC502 | Raises in body but not documented          |
| DOC503 | Raises in docstring don't match body       |
| DOC504 | Raises `AssertionError` but not documented |

### Class Attributes (DOC6xx)

| Code   | Description                             |
| ------ | --------------------------------------- |
| DOC601 | Class has fewer attributes in docstring |
| DOC602 | Class has more attributes in docstring  |
| DOC603 | Class attributes differ from docstring  |
| DOC604 | Class attributes in different order     |
| DOC605 | Class attribute type hints don't match  |

## Configuration Options

pydoclint automatically reads `pyproject.toml` with `[tool.pydoclint]` section. Options
use dashes in TOML (e.g., `arg-type-hints-in-docstring`).

### Style

pydoclint supports three docstring styles:

- `numpy` - NumPy-style docstrings (pydoclint's native default)
- `google` - Google-style docstrings (lintro's default)
- `sphinx` - Sphinx-style docstrings

Note: While pydoclint defaults to `numpy`, lintro defaults to `google` to match common
project conventions.

### Type Hint Location Options

| Option                        | Default | Recommended | Description                                        |
| ----------------------------- | ------- | ----------- | -------------------------------------------------- |
| `arg-type-hints-in-docstring` | `true`  | `false`     | Require types in docstring Args section            |
| `arg-type-hints-in-signature` | `true`  | `true`      | Require type annotations in signatures             |
| `check-return-types`          | `true`  | `false`     | Validate return types match between doc/annotation |

Setting `arg-type-hints-in-docstring = false` eliminates DOC105, DOC109, DOC110 errors
that require duplicating type information already present in annotations.

Setting `check-return-types = false` eliminates DOC203 errors for return type
mismatches.

### Validation Options

| Option                           | Default | Description                                |
| -------------------------------- | ------- | ------------------------------------------ |
| `check-arg-order`                | `true`  | Verify argument order matches signature    |
| `skip-checking-short-docstrings` | `true`  | Skip validation for single-line docstrings |
| `quiet`                          | `true`  | Suppress non-error output                  |

## Lintro Configuration

pydoclint reads its configuration directly from `[tool.pydoclint]` in `pyproject.toml`.
Lintro-specific options (like timeout) go in `[tool.lintro.pydoclint]`:

```toml
# Native pydoclint configuration (read by pydoclint directly)
[tool.pydoclint]
style = "google"
arg-type-hints-in-docstring = false
arg-type-hints-in-signature = true
check-return-types = false
check-arg-order = true
skip-checking-short-docstrings = true

# Lintro-specific options
[tool.lintro.pydoclint]
timeout = 30
```

Override via command line:

```bash
lintro chk --tools pydoclint --tool-options pydoclint:style=numpy
```

## Integration Notes

- Priority: 45 (runs before formatters)
- Does not support auto-fix (documentation must be fixed manually)
- Works well with ruff's D (pydocstyle) rules for complementary coverage

## Ruff D/DOC vs Standalone pydoclint

Ruff provides two docstring-related rule sets:

- **D rules (pydocstyle)**: Style and formatting checks
- **DOC rules (ruff's pydoclint)**: Limited semantic validation (subset of standalone
  pydoclint)

### Comparison Table

| Aspect                    | Ruff D (pydocstyle) | Ruff DOC         | Standalone pydoclint |
| ------------------------- | ------------------- | ---------------- | -------------------- |
| **Focus**                 | Style/format        | Limited semantic | Full semantic        |
| **Docstring presence**    | D100-D107           | -                | -                    |
| **Formatting**            | D200-D215           | -                | -                    |
| **Punctuation/style**     | D300-D409           | -                | -                    |
| **Missing returns**       | -                   | DOC201           | DOC201               |
| **Extraneous returns**    | -                   | DOC202           | DOC202               |
| **Missing yields**        | -                   | DOC402           | DOC402-404           |
| **Missing exceptions**    | -                   | DOC501           | DOC501-503           |
| **Arg mismatches**        | -                   | DOC102 only      | DOC101-111           |
| **Class attributes**      | -                   | -                | DOC601-605           |
| **`__init__` docstrings** | -                   | -                | DOC301-306           |

### Summary

- **Ruff D rules** handle **format** (presence, indentation, punctuation, style)
- **Standalone pydoclint** handles **content accuracy** (arguments match, types match,
  raises documented)
- **Ruff DOC rules** provide a small subset of pydoclint functionality
- Using both ruff D and standalone pydoclint provides the most comprehensive coverage

## Best Practices

1. **Use with ruff D rules**: pydoclint validates content, ruff D validates format
2. **Set consistent style**: Match your project's docstring convention
3. **Enable check-arg-order**: Catches documentation that doesn't match signature order
4. **Skip short docstrings**: Single-line docstrings often don't need full documentation

## Example Usage

```bash
# Check with default settings
lintro chk --tools pydoclint .

# Check with NumPy style
lintro chk --tools pydoclint --tool-options pydoclint:style=numpy .

# Check specific files
lintro chk --tools pydoclint src/module.py
```
