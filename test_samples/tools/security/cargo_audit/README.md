# cargo-audit Test Samples

This directory contains a minimal Rust project for testing cargo-audit integration.

## Sample Files

- `Cargo.toml` - Minimal project configuration with no dependencies
- `Cargo.lock` - Lock file with no external dependencies (no vulnerabilities)

## Why No Vulnerabilities?

The sample intentionally has no external dependencies because:

1. **Security tool**: cargo-audit scans for known security vulnerabilities
2. **External database**: Vulnerabilities are checked against the RustSec advisory database
3. **Unstable samples**: Creating Cargo.lock files with known vulnerabilities would require:
   - Specific vulnerable crate versions that may no longer exist
   - Network access to crates.io
   - Regular updates as advisories change

## Testing Approach

In addition to these sample files, cargo-audit tests use:

- **Mock JSON output**: Parser tests use sample JSON matching cargo-audit's output format
- **Mocked subprocess calls**: Plugin tests mock the cargo-audit command execution

See the unit tests for examples:

- `tests/unit/parsers/test_cargo_audit_parser.py`
- `tests/unit/tools/cargo_audit/test_cargo_audit_plugin.py`
