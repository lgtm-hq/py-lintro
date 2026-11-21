# OSV-Scanner Tool Analysis

## Overview

OSV-Scanner is Google's vulnerability scanner that uses the Open Source Vulnerabilities
(OSV) database. It scans lockfiles and SBOMs for known vulnerabilities across multiple
ecosystems (PyPI, npm, Go, Rust, Ruby, PHP, .NET, Java, and more). This analysis
compares Lintro's wrapper with the core OSV-Scanner tool.

## Core Tool Capabilities

- **Multi-ecosystem scanning**: Supports lockfile formats across several ecosystems; the
  set depends on the installed release
- **Lockfile scanning**: `--lockfile <path>` for precise per-file scanning
- **Recursive scanning**: `--recursive` to discover lockfiles in directory trees
- **SBOM scanning**: `--sbom <path>` for CycloneDX and SPDX formats
- **Output formats**: `--format json|table|markdown|sarif`
- **Configuration**: `.osv-scanner.toml` for ignoring vulns, overriding packages
- **Guided remediation**: `--experimental-resolution-strategy` for fix suggestions

## Lintro Implementation Analysis

### ✅ Preserved Features

- ✅ Recursive lockfile discovery performed by OSV-Scanner itself
- ✅ JSON output (`--format json`) with structured parsing
- ✅ Vulnerability grouping with severity extraction from CVSS
- ✅ Fixed-version extraction from affected data
- ✅ Multi-lockfile discovery using the native scanner’s supported formats

### ⚠️ Defaults and Notes

- ⚠️ Forces `--format json` to ensure parseable output
- ⚠️ Uses `scan --recursive` and bypasses Lintro’s usual file discovery pipeline
- ⚠️ Default timeout of 120 seconds (network operations)
- ⚠️ Returns non-zero exit code when vulnerabilities are found (expected behavior)

### 🚀 Enhancements

- ✅ Normalized `ToolResult` with structured `OsvScannerIssue` objects
- ✅ Severity extraction from group `max_severity` fields
- ✅ Fixed-version extraction from vulnerability `affected` data
- ✅ Stable parsing across OSV-Scanner v2 output format
- ✅ Suppression staleness detection via probe scan (`--config /dev/null`)
- ✅ Classification of `.osv-scanner.toml` entries as Active/Stale/Expired
- ✅ Suppression metadata surfaced in summary table and JSON output

## Usage Comparison

### Core OSV-Scanner

```bash
osv-scanner scan --format json --lockfile requirements.txt
osv-scanner scan --recursive .
osv-scanner scan --sbom sbom.json
```

### Lintro Wrapper

```python
plugin = get_plugin("osv_scanner")
result = plugin.check(["."], {})
```

## Configuration Strategy

- Finds `.osv-scanner.toml` by walking upward from the scan root and passes it
  explicitly
- Supports runtime options via `set_options()` and `--tool-options`
- `check_suppressions` option (default: true) enables probe scan for staleness detection
- Probe scan uses `--config /dev/null` to bypass all suppressions

## ⚠️ Limited/Missing Features

- ⚠️ SBOM scanning (`--sbom`) not exposed
- Recursive scanning is already shipped; it is not a proposed pass-through.
- ⚠️ Guided remediation (`--experimental-resolution-strategy`) not exposed
- ⚠️ License scanning not exposed

### 🔧 Proposed runtime pass-throughs

- Recursive scanning needs no proposed option; it is the current wrapper behavior.
- SBOM selection is an unimplemented idea; use native `osv-scanner scan --sbom` instead.

## Recommendations

- Use `lintro check . --tools osv_scanner` for recursive lockfile scanning. SBOM
  scanning requires the native CLI; the proposed Lintro SBOM option is unavailable.
