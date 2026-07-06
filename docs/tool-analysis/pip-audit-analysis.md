# pip-audit Tool Analysis

## Overview

[pip-audit](https://github.com/pypa/pip-audit) is the Python Packaging Authority
(PyPA) tool for scanning Python dependencies for packages with known
vulnerabilities. It queries the [PyPI Advisory Database](https://github.com/pypa/advisory-database)
and OSV, complementing `bandit` (which scans source code) by scanning the
dependency surface instead. This analysis compares Lintro's wrapper with the
core pip-audit tool.

## Core Tool Capabilities

- **Environment scanning**: audits the active Python environment by default
- **Requirements scanning**: `-r <requirements.txt>` audits a requirements file
- **Project scanning**: a positional `project_path` audits a local project
  (reads `pyproject.toml`)
- **Data sources**: PyPI Advisory Database and OSV (`-s`/`--vulnerability-service`)
- **Output formats**: `--format columns|json|cyclonedx-json|cyclonedx-xml|markdown`
- **Aliases and descriptions**: `--aliases` and `--desc` enrich JSON output
- **Experimental fixing**: `--fix` upgrades vulnerable dependencies in place

## Parser Choice: Native JSON (not SARIF)

pip-audit does **not** emit SARIF. Its `--format` choices are `columns`, `json`,
`cyclonedx-json`, `cyclonedx-xml`, and `markdown` (verified against pip-audit
2.10.1). Applying the fidelity checklist in
`docs/design/sarif-ingestion-evaluation.md`, there is no SARIF path to evaluate —
Lintro parses pip-audit's native `--format json` output with a dedicated parser
(`lintro/parsers/pip_audit/`), the same approach used for the other dependency
scanners (`cargo-audit`, `cargo-deny`).

For reference, had SARIF been available, the native path still preserves more
than a generic SARIF ingest would: per-vulnerability `fix_versions`, CVE/GHSA
`aliases`, and the PYSEC/GHSA advisory `id` used to build a stable OSV doc URL.

## JSON Output Schema

`pip-audit --format json` emits a single top-level object:

```json
{
  "dependencies": [
    {
      "name": "jinja2",
      "version": "2.4.1",
      "vulns": [
        {
          "id": "PYSEC-2019-217",
          "fix_versions": ["2.10.1"],
          "aliases": ["CVE-2019-10906", "GHSA-462w-v97r-4m45"],
          "description": "Jinja2 sandbox escape via str.format."
        }
      ]
    },
    { "name": "somepkg", "skip_reason": "could not be audited" }
  ],
  "fixes": []
}
```

Note: the JSON payload has **no severity field**. Lintro therefore reports
severity as `UNKNOWN` (normalized to `WARNING` for display) rather than
fabricating a level. Skipped dependencies (those carrying a `skip_reason`
instead of `vulns`) are ignored.

## Lintro Implementation Analysis

### ✅ Preserved Features

- ✅ Requirements-file scanning via `-r <file>` for each discovered
  `requirements*.txt`
- ✅ Project scanning via the positional `project_path` for discovered
  `pyproject.toml` / `setup.py` (de-duplicated per directory)
- ✅ JSON output (`--format json`) with structured parsing
- ✅ Vulnerability ID, package name/version, fix versions, and aliases extracted

### ⚠️ Defaults and Notes

- ⚠️ Forces `--format json --progress-spinner off` for stable, parseable output
- ⚠️ Default timeout of 120 seconds (network operations can be slow)
- ⚠️ Returns non-zero exit code when vulnerabilities are found (expected)
- ⚠️ Severity is `UNKNOWN` because pip-audit's JSON omits it
- ⚠️ Parses stdout only so stderr warnings cannot corrupt the JSON (see #1043)
- ⚠️ Fails closed on non-empty, unparseable stdout — a security scanner must
  never report an unparseable run as a clean pass (see #1044)

### 🚀 Enhancements

- ✅ Normalized `ToolResult` with structured `PipAuditIssue` objects
- ✅ One issue per (dependency, vulnerability) pair
- ✅ Stable OSV documentation URL derived from the advisory ID
  (`https://osv.dev/vulnerability/{id}`)

## Usage Comparison

### Core pip-audit

```bash
pip-audit --format json -r requirements.txt
pip-audit --format json .          # audit a local project
pip-audit --format json            # audit the current environment
```

### Lintro Wrapper

```python
plugin = get_plugin("pip_audit")
result = plugin.check(["requirements.txt", "pyproject.toml"], {})
```

## Installation

```bash
pip install pip-audit    # or: uv add pip-audit
```

pip-audit ships in Lintro's `tools` extra and is installed by
`scripts/utils/install-tools.sh` (and the Docker images).

## Comparison with Related Tools

| Tool          | Scans                    | Ecosystem            | Source DB                       |
| ------------- | ------------------------ | -------------------- | ------------------------------- |
| **pip-audit** | Dependencies             | Python only          | PyPI Advisory DB + OSV          |
| bandit        | Source code (AST)        | Python only          | Bandit plugin rules             |
| osv-scanner   | Lockfiles/SBOMs          | Multi-ecosystem      | OSV                             |
| cargo-audit   | `Cargo.lock`             | Rust only            | RustSec advisory DB             |

pip-audit and bandit are complementary: bandit finds insecure code patterns,
pip-audit finds vulnerable dependencies. pip-audit overlaps with osv-scanner on
Python dependency vulnerabilities but offers deeper Python-specific integration
(virtualenv scanning, requirements resolution, `pip-audit --fix`); see issues
#423 (Trivy) and #435 (OSV-Scanner) for the broader multi-ecosystem tools.

## ⚠️ Limited/Missing Features

- ⚠️ `--fix` auto-remediation is not driven by Lintro (dependencies are updated
  manually)
- ⚠️ CycloneDX SBOM output formats are not exposed
- ⚠️ Custom `--vulnerability-service` / `--osv-url` selection is not exposed

## Recommendations

- Use Lintro defaults for stable CI JSON scanning of requirements files and
  project manifests. Run `pip-audit --fix` manually when auto-remediation is
  desired.
