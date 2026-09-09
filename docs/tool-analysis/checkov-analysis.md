# Checkov Tool Analysis

## Overview

[Checkov](https://www.checkov.io/) is a static analysis tool for Infrastructure-as-Code
(IaC) that detects security and compliance misconfigurations. Checkov ships 1000+
built-in policies covering CIS benchmarks, SOC2, HIPAA, and PCI-DSS across Terraform,
CloudFormation, Kubernetes, ARM, Serverless, and Dockerfiles.

Lintro integrates Checkov as a **security / infrastructure** tool, scoped to Terraform,
running in a hermetic offline mode. This document records the parser choice (native JSON
vs SARIF), the scope decision, and the offline guarantees.

## Scope decision: Terraform only

Two independent narrowings. The plugin pins `--framework terraform,terraform_json`,
because checkov otherwise runs every framework it supports — including the secrets
framework, over the same `.tf` files — and secrets are gitleaks' and trufflehog's
surface in lintro. Both Terraform frameworks are named because checkov routes HCL
(`*.tf`) through `terraform` and Terraform's JSON syntax (`*.tf.json`) through a
separate `terraform_json` runner; a pin naming only the former would evaluate zero
policies against a claimed `.tf.json` file and still exit 0. And its file matchers are
deliberately narrow:

```python
file_patterns = ["*.tf", "*.tf.json"]
```

Rationale:

- **No Dockerfile overlap.** Checkov can scan Dockerfiles, but lintro already ships
  **hadolint** (`file_patterns = ["Dockerfile", "Dockerfile.*"]`) for that surface.
  Including `Dockerfile*` here would double-report the same file under two tools with
  different rule sets and severities. Dockerfiles are therefore left to hadolint.
- **No broad YAML/JSON globs.** The issue's suggested patterns included `*.yaml`,
  `*.yml`, and `*.json`. lintro discovers files and passes them to the tool
  individually, so those globs would feed every `package.json`, CI config, and arbitrary
  YAML document to Checkov — producing parse noise and slow scans on non-IaC files.
  Terraform's `.tf` / `.tf.json` extensions are unambiguous IaC and collide with no
  other lintro tool.
- **Room to grow.** CloudFormation and Kubernetes support can be added later behind
  content-based detection without disturbing this precise baseline.

## Offline / hermetic guarantees

Checkov can query the Prisma Cloud / Bridgecrew platform and download external policies
and Terraform modules. The plugin keeps every run offline:

- `--skip-download` — never downloads external policies from the registry.
- `--download-external-modules False` — never fetches remote Terraform modules.
- `--skip-results-upload` — findings never leave the machine.
- **No `--bc-api-key` is ever passed** — enforced by construction: the command builder
  has no code path that adds an API key.

All three flags are unconditional: no `--tool-options` value can turn them off, so no
lintro option can talk a run into fetching policies or modules or uploading a finding,
and `--bc-api-key` has no code path at all.

The one thing lintro does not control is the environment: checkov also reads
`BC_API_KEY` / `PRISMA_API_URL` from the process environment, so a shell that already
exports a platform key puts checkov in platform mode regardless of the argv lintro
builds. That is the one remaining route to an outbound request; unset those variables if
a run must make none. It does not buy enrichment: `--skip-download` short-circuits
`bc_integration.get_platform_run_config()` before any run-config call, so the policy
metadata that would fill `severity` and `guideline` is never fetched, keyed or not (see
[Severity behavior](#severity-behavior)).

## Parser choice: native JSON, not SARIF

Checkov emits both `--output json` and `--output sarif`. Per the fidelity checklist in
[`docs/design/sarif-ingestion-evaluation.md`](../design/sarif-ingestion-evaluation.md)
(#1066 / PR #1140), both formats were captured from the **same run** on a seeded
Terraform fixture (checkov 3.3.6, no platform API key) and compared:

| Signal               | Native JSON                                   | Checkov SARIF                                                |
| -------------------- | --------------------------------------------- | ------------------------------------------------------------ |
| Check ID             | `check_id` (e.g. `CKV_AWS_260`)               | `ruleId` (preserved)                                         |
| Resource attribution | `resource` (`aws_security_group.allow_all`)   | **absent from `results[]`** — only prose in `rule.help.text` |
| Severity             | `severity: null` (always, see below)          | **hard-coded `level: "error"` for every result**             |
| Guideline / doc URL  | `guideline: null` (always, see below)         | rule has **no `helpUri`**                                    |
| Fix metadata         | `fixed_definition` (present when applicable)  | **no `fixes[]`**                                             |
| File / line          | `file_path` + `file_line_range` `[start,end]` | `uri` + `region.startLine/endLine`                           |

### Why SARIF is lossy here

- **Fabricated severity.** Checkov severity comes from platform metadata that
  `--skip-download` — passed on every lintro run — suppresses, so the JSON `severity` is
  an honest `null` (lintro falls back to its default), whereas SARIF stamps **`error` on
  every finding**, over-stating severity uniformly. This is a fidelity _loss_, not a
  gain.
- **Lost resource attribution.** The failed resource address — Checkov's most useful
  piece of enrichment — is a first-class `resource` field in JSON but is dropped from
  SARIF `results[]` (it survives only as free text inside the rule's `help.text`, which
  cannot be reliably parsed back into a field).
- **No doc URLs.** Checkov SARIF omits `helpUri`, so a SARIF path yields no
  documentation links; the native path synthesizes a stable policy-index URL and would
  prefer checkov's own `guideline` URL if a report ever carried one (under lintro it
  never does — see [Severity behavior](#severity-behavior)).

Conclusion: the shared SARIF parser would be **lossy** for Checkov (fabricated severity,
dropped resource attribution, no doc URLs). A **native JSON parser** is used, preserving
check ID, resource, line range, and honest severity/guideline semantics. Checkov is not
among the evaluation's SARIF-native candidates.

## Lintro implementation

- **Definition:** `lintro/tools/checkov/definition.py` —
  `ToolType.SECURITY | ToolType.INFRASTRUCTURE`, `can_fix=False`, one
  `Claim(patterns=["*.tf", "*.tf.json"], capabilities={Cap.CHECK})`. There is no
  priority scalar: execution order is derived from the claims (#2427), and a check-only
  claim on patterns no other tool touches leaves checkov unordered against everything
  else. Run `lintro check --explain-order` to see where it lands.
- **Partitioning:** `partitionable=False`. Checkov's graph checks (the `CKV2_*` family)
  evaluate relations between resources, so a resource defined in one file and referenced
  from another must be visible in a single invocation; sharding the file list would
  silently drop those findings.
- **Parser:** `lintro/parsers/checkov/` — `parse_checkov_output()` surfaces only
  `results.failed_checks`, tolerates both the single-object (one framework) and
  list-of-objects (multi-framework) JSON shapes, and is defensive against malformed
  input.
- **Issue model:** `CheckovIssue(BaseIssue)` carries `check_id`, `check_name`,
  `resource`, `check_class`, native `severity`, `guideline`, and `end_line`. The display
  message includes the resource address; the `guideline` URL, when present, becomes the
  issue's `doc_url`.

### Severity behavior

Checkov's severity comes from platform metadata that `--skip-download` suppresses.
`checkov/main.py` sets `bc_integration.skip_download = True` from that flag before
calling `get_platform_run_config()`, which then returns immediately — so an operator's
`BC_API_KEY` cannot restore the field either. Under lintro findings always have
`severity = None` and normalize to lintro's default `WARNING`. The parser still reads
the field, so if a report ever carries one the native `CRITICAL`/`HIGH`/`MEDIUM`/`LOW`
values are honored through lintro's severity alias table (`CRITICAL`/`HIGH` → ERROR,
`MEDIUM` → WARNING, `LOW` → INFO).

## Files checkov cannot parse

Checkov derives its exit code from failed checks alone, so a `.tf` file it cannot read
is listed under `results.parsing_errors` while `failed_checks` stays empty and the exit
code stays 0. Reporting that as a clean scan would claim a file was analysed when no
policy ran against it, so the parser emits one `CKV_PARSE_ERROR` issue per entry and the
run fails.

## Installation

Checkov pulls a large dependency tree (boto3, cyclonedx, spdx-tools, rustworkx, …), so
it is **not** a bundled lintro dependency. It is installed in an isolated environment:

```bash
# Pinned to the version the manifest gate compares against:
scripts/utils/install-tools.sh --local --tools checkov
```

That runs `uv tool install checkov==<pin>`. A bare `uv tool install checkov` also
isolates the tool correctly but installs whatever PyPI serves that day, which clears the
`min_version` floor without giving the pin equality described below. Never
`pip install checkov` into lintro's own environment (see below).

The isolation is not merely a size preference. Checkov requires `packaging>=23.0,<24.0`
while lintro requires `packaging>=25.0`, so the two cannot share a resolution at all:
adding `checkov` to any `pyproject.toml` extra makes `uv lock` unsatisfiable. This is
the same class of constraint that keeps semgrep out of lintro's resolver (#2104).

A `requirements-checkov.txt` pin was rejected for a second, independent reason: this
repository's own `osv-scanner` dogfood scans `requirements*.txt` and resolves it
transitively, so declaring checkov's dependency tree here would report 45 known
advisories in packages lintro never installs and turn the security gate permanently red.

The pin therefore lives in `TOOL_VERSIONS` in `lintro/_tool_versions.py`, where the
manifest generator reads it, and Renovate tracks it against the `checkov` PyPI package
through a custom manager in `renovate.json`. The manifest entry declares
`install.type = "binary"` — the manifest's label for "an executable that lands on PATH
via `install-tools.sh`" rather than a statement about upstream packaging (cppcheck
carries the same type while arriving from apt). Declaring `pip` would point quick-fix at
`uv pip install checkov` in lintro's own environment, which downgrades `packaging` and
breaks lintro.

`scripts/utils/install-tools.sh` installs exactly that pin with
`uv tool install checkov==<pin>`, so the version on `PATH` equals the manifest version —
the equality the manifest-vs-image gate (#1511) requires.

## Default selection

Checkov is listed in `language_map.security` as well as `terraform`. A no-config
`lintro check` that detects Terraform (or any run that includes the security set) will
invoke Checkov when the binary is on `PATH`. That can fail previously green Terraform
CI. Opt out with `tools.checkov.enabled: false`, or skip policies with
`--tool-options checkov:skip_checks=CKV_…`.

## Limitations

- **No autofix.** Checkov reports misconfigurations only; `fix()` raises
  `NotImplementedError`. Run `lintro check` to see issues.
- **Severity/guideline are always `null`** — `--skip-download` is unconditional and
  suppresses the platform metadata that would carry them (see
  [Severity behavior](#severity-behavior)).
- **Terraform-scoped** by design (see [Scope decision](#scope-decision-terraform-only)).
