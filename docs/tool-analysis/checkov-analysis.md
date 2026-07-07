# Checkov Tool Analysis

## Overview

[Checkov](https://www.checkov.io/) is a static analysis tool for
Infrastructure-as-Code (IaC) that detects security and compliance
misconfigurations. Checkov ships 1000+ built-in policies covering CIS
benchmarks, SOC2, HIPAA, and PCI-DSS across Terraform, CloudFormation,
Kubernetes, ARM, Serverless, and Dockerfiles.

Lintro integrates Checkov as a **security / infrastructure** tool, scoped to
Terraform, running in a hermetic offline mode. This document records the parser
choice (native JSON vs SARIF), the scope decision, and the offline guarantees.

## Scope decision: Terraform only

Checkov's file matchers are deliberately narrow:

```python
file_patterns = ["*.tf", "*.tf.json"]
```

Rationale:

- **No Dockerfile overlap.** Checkov can scan Dockerfiles, but lintro already
  ships **hadolint** (`file_patterns = ["Dockerfile", "Dockerfile.*"]`) for that
  surface. Including `Dockerfile*` here would double-report the same file under
  two tools with different rule sets and severities. Dockerfiles are therefore
  left to hadolint.
- **No broad YAML/JSON globs.** The issue's suggested patterns included
  `*.yaml`, `*.yml`, and `*.json`. lintro discovers files and passes them to the
  tool individually, so those globs would feed every `package.json`, CI config,
  and arbitrary YAML document to Checkov — producing parse noise and slow scans
  on non-IaC files. Terraform's `.tf` / `.tf.json` extensions are unambiguous
  IaC and collide with no other lintro tool.
- **Room to grow.** CloudFormation and Kubernetes support can be added later
  behind content-based detection without disturbing this precise baseline.

## Offline / hermetic guarantees

Checkov can query the Prisma Cloud / Bridgecrew platform and download external
policies and Terraform modules. The plugin keeps every run offline:

- `--skip-download` — never downloads external policies from the registry.
- `--download-external-modules False` — never fetches remote Terraform modules.
- **No `--bc-api-key` is ever passed** — without an API key Checkov performs no
  results upload. This is enforced by construction: the command builder has no
  code path that adds an API key.

These flags are the plugin defaults (`skip_download=True`); disabling them is
possible via `--tool-options checkov:skip_download=false` for users who
explicitly want network access.

## Parser choice: native JSON, not SARIF

Checkov emits both `--output json` and `--output sarif`. Per the fidelity
checklist in [`docs/design/sarif-ingestion-evaluation.md`](../design/sarif-ingestion-evaluation.md)
(#1066 / PR #1140), both formats were captured from the **same run** on a seeded
Terraform fixture (checkov 3.3.6, no platform API key) and compared:

| Signal              | Native JSON                                  | Checkov SARIF                                        |
| ------------------- | -------------------------------------------- | ---------------------------------------------------- |
| Check ID            | `check_id` (e.g. `CKV_AWS_260`)              | `ruleId` (preserved)                                 |
| Resource attribution| `resource` (`aws_security_group.allow_all`)  | **absent from `results[]`** — only prose in `rule.help.text` |
| Severity            | `severity: null` (platform key required)     | **hard-coded `level: "error"` for every result**     |
| Guideline / doc URL | `guideline: null` (platform key required)    | rule has **no `helpUri`**                            |
| Fix metadata        | `fixed_definition` (present when applicable) | **no `fixes[]`**                                     |
| File / line         | `file_path` + `file_line_range` `[start,end]`| `uri` + `region.startLine/endLine`                   |

### Why SARIF is lossy here

- **Fabricated severity.** Checkov severity is only populated with a platform
  API key. In offline mode the JSON `severity` is an honest `null` (lintro falls
  back to its default), whereas SARIF stamps **`error` on every finding**,
  over-stating severity uniformly. This is a fidelity *loss*, not a gain.
- **Lost resource attribution.** The failed resource address — Checkov's most
  useful piece of enrichment — is a first-class `resource` field in JSON but is
  dropped from SARIF `results[]` (it survives only as free text inside the
  rule's `help.text`, which cannot be reliably parsed back into a field).
- **No doc URLs.** Checkov SARIF omits `helpUri`, so a SARIF path yields no
  documentation links; the native path synthesizes a stable policy-index URL and
  prefers Checkov's own `guideline` URL when a platform key provides one.

Conclusion: the shared SARIF parser would be **lossy** for Checkov (fabricated
severity, dropped resource attribution, no doc URLs). A **native JSON parser** is
used, preserving check ID, resource, line range, and honest severity/guideline
semantics. Checkov is not among the evaluation's SARIF-native candidates.

## Lintro implementation

- **Definition:** `lintro/tools/definitions/checkov.py` —
  `ToolType.SECURITY | ToolType.INFRASTRUCTURE`, `can_fix=False`, priority 88.
- **Parser:** `lintro/parsers/checkov/` — `parse_checkov_output()` surfaces only
  `results.failed_checks`, tolerates both the single-object (one framework) and
  list-of-objects (multi-framework) JSON shapes, and is defensive against
  malformed input.
- **Issue model:** `CheckovIssue(BaseIssue)` carries `check_id`, `check_name`,
  `resource`, `check_class`, native `severity`, `guideline`, and `end_line`. The
  display message includes the resource address; the `guideline` URL, when
  present, becomes the issue's `doc_url`.

### Severity behavior

Because Checkov severity requires a platform API key, offline findings have
`severity = None` and normalize to lintro's default `WARNING`. When a key is
present, native `CRITICAL`/`HIGH`/`MEDIUM`/`LOW` values are honored via lintro's
severity alias table (`CRITICAL`/`HIGH` → ERROR, `MEDIUM` → WARNING,
`LOW` → INFO).

## Installation

Checkov pulls a large dependency tree (boto3, cyclonedx, spdx-tools, rustworkx,
…), so it is **not** a bundled lintro dependency. It is installed in an isolated
environment:

```bash
uv tool install checkov
# or
pip install checkov
```

`scripts/utils/install-tools.sh` performs the isolated install via
`uv tool install`, placing the `checkov` shim on `PATH` while keeping its
dependencies out of lintro's own environment. The pinned version lives in
`lintro/_tool_versions.py` (`ToolName.CHECKOV`) and is kept current by a Renovate
custom manager against the `checkov` PyPI package.

## Limitations

- **No autofix.** Checkov reports misconfigurations only; `fix()` raises
  `NotImplementedError`. Run `lintro check` to see issues.
- **Severity/guideline require a platform key** (see above).
- **Terraform-scoped** by design (see [Scope decision](#scope-decision-terraform-only)).
