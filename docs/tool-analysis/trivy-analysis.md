# Trivy Tool Analysis

## Overview

[Trivy](https://trivy.dev/) is a comprehensive security scanner by Aqua
Security. It can scan container images, filesystems, git repositories,
Kubernetes clusters, and SBOMs for OS-package and language-specific
vulnerabilities, IaC misconfigurations, exposed secrets, and license issues.

Lintro integrates Trivy as a **security** tool, scoped narrowly to *filesystem
dependency-vulnerability* scanning of lockfiles and manifests
(`trivy fs --scanners vuln`), running hermetically by default. This document
records the scope decision, the parser choice (native JSON vs SARIF), and the
vulnerability-database hermeticity handling.

## Scope decision: dependency-vulnerability scanning only

Trivy is a Swiss-army-knife scanner, but lintro deliberately uses only its
dependency-vulnerability surface. The plugin's file matchers target dependency
lockfiles and manifests:

```python
file_patterns = [
    "requirements.txt", "Pipfile.lock", "poetry.lock", "uv.lock",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock",
    "go.mod", "Cargo.lock", "Gemfile.lock", "composer.lock",
    "gradle.lockfile", "pom.xml",
]
```

and the command fixes `--scanners vuln`. Rationale:

- **No secret-scanning overlap.** Trivy can detect secrets, but lintro already
  ships **gitleaks** (and trufflehog, PR #1149) for that surface. Enabling
  Trivy's secret scanner would double-report the same leaked credentials under
  two tools. Secrets are left to gitleaks/trufflehog.
- **No IaC-misconfiguration overlap.** Trivy can scan Terraform/Dockerfiles for
  misconfigurations, but that is **checkov**'s scope (PR #1156, Terraform) and
  **hadolint**'s (Dockerfiles). Enabling Trivy's `config`/`misconfig` scanner
  would collide with both. IaC misconfig is left to checkov and hadolint.
- **Complements, does not replace, osv-scanner.** lintro already has
  **osv-scanner**, which is also multi-ecosystem dependency-vulnerability
  scanning. Trivy is kept because it draws on a **different aggregated advisory
  database** — GitHub Security Advisories, NVD, and per-distro/vendor advisories
  (Red Hat, Ubuntu, GHSA) with multi-vendor CVSS — whereas osv-scanner queries
  the OSV.dev database. The two produce overlapping but non-identical CVE sets;
  running both is defense-in-depth, exactly the "different detection methods"
  rationale in issue #423. Trivy is scoped only to lockfiles/manifests so the
  overlap is bounded and predictable.
- **No relationship to the SBOM / grype CI pipeline.** That pipeline scans the
  built artifact/image; this plugin scans source-tree lockfiles at lint time.

## Parser choice: native JSON, not SARIF

Trivy emits both `--format json` and `--format sarif`. Per the fidelity
checklist in [`docs/design/sarif-ingestion-evaluation.md`](../design/sarif-ingestion-evaluation.md)
(#1066 / PR #1140), **both formats were captured from the same run** on a seeded
`requirements.txt` (trivy 0.72.0, DB present) and compared for the same
vulnerability (`CVE-2019-14234`, Django 2.2.0):

| Signal                 | Native JSON                                             | Trivy SARIF                                              |
| ---------------------- | ------------------------------------------------------- | ------------------------------------------------------- |
| CVE id                 | `VulnerabilityID` (`CVE-2019-14234`)                    | `ruleId` (preserved)                                    |
| Advisory ids           | `VendorIDs` (`GHSA-6r97-cj55-9hrq`)                     | **dropped** — not present in results or rules           |
| Severity fidelity      | `Severity` — 5 levels (`CRITICAL`/`HIGH`/`MEDIUM`/`LOW`/`UNKNOWN`) | `level` — pre-collapsed at the source (`CRITICAL`+`HIGH` → `error`); CRITICAL vs HIGH is **lost** |
| Fixed version          | `FixedVersion` — structured field (`1.11.23, 2.1.11, 2.2.4`) | only as **free-text prose** inside `message.text` / `help.text` |
| Installed version      | `InstalledVersion` — structured field                   | only as prose in `message.text`                         |
| Package attribution    | `PkgName` — structured field                            | only as prose (`Package: Django`) in `message.text`     |
| Target attribution     | `Results[].Target` (`requirements.txt`)                 | `uri` (preserved)                                       |
| Doc URL                | `PrimaryURL` (`https://avd.aquasec.com/nvd/...`)        | `helpUri` (preserved)                                   |
| CVSS scores / CWE ids  | `CVSS` (multi-vendor), `CweIDs`                          | **dropped**                                             |

### Why SARIF is lossy here

- **Severity is pre-collapsed at the source.** For a vulnerability scanner the
  CRITICAL/HIGH/MEDIUM/LOW ranking is the primary prioritization signal. Trivy's
  SARIF writer maps CRITICAL **and** HIGH both to `level: "error"`, so the two
  most important tiers are indistinguishable in SARIF. Native JSON preserves the
  exact `Severity` string, which lintro surfaces in the issue message even
  though its own severity column normalizes (CRITICAL/HIGH → ERROR).
- **Structured remediation data becomes prose.** `FixedVersion`,
  `InstalledVersion`, and `PkgName` are first-class JSON fields but appear in
  SARIF only as human-readable sentences inside `message.text`. Recovering
  "fixed in version X" from SARIF would require regex-parsing English prose —
  the opposite of the resilience SARIF is supposed to buy.
- **Advisory ids, CVSS, and CWE are dropped.** `VendorIDs` (GHSA ids), the
  multi-vendor `CVSS` block, and `CweIDs` have no SARIF equivalent in Trivy's
  output.

`ruleId`/CVE, `uri`/target, and `helpUri`/doc-URL are the only lossless mappings.

Conclusion: the shared SARIF parser would be **lossy** for Trivy (collapsed
severity, prose-only fixed/installed versions and package names, dropped
advisory/CVSS/CWE metadata). A **native JSON parser** is used, preserving the CVE
id, package, installed and fixed versions, honest 5-level severity, target, and
doc URL. Trivy is not among the SARIF-native candidates in the evaluation.

## Vulnerability database / hermeticity

Trivy scans against a local vulnerability database that it normally downloads and
refreshes over the network. To keep a `lintro` run hermetic and non-hanging, the
plugin:

- passes `--skip-db-update` by default — a run **never** triggers a DB download;
- passes `--offline-scan` by default — a scan **never** calls external advisory
  APIs (e.g. Maven Central for `pom.xml`);
- bounds every invocation with a timeout (default **300s**), so even a run that
  is explicitly allowed to download the DB cannot hang indefinitely;
- detects the "database not present" condition (which `--skip-db-update` raises
  when no DB is cached) and returns a **clear, non-blocking skip** — success
  with an explanatory message — instead of failing closed or hanging.

To populate the DB once (requires network):

```bash
trivy fs --download-db-only
# or, for a single lintro run:
lintro check --tools trivy --tool-options "trivy:skip_db_update=false"
```

Integration tests are gated on both the `trivy` binary **and** DB availability:
if the DB is missing, they skip rather than fail, so CI without a DB stays green.

## Lintro implementation

- **Definition:** `lintro/tools/definitions/trivy.py` — `ToolType.SECURITY`,
  `can_fix=False`, priority 87. Runs `trivy fs --scanners vuln --format json
  --quiet [--skip-db-update] [--offline-scan]` once per matched lockfile (Trivy
  accepts one target path per invocation) and aggregates the findings.
- **Parser:** `lintro/parsers/trivy/` — `parse_trivy_output()` flattens
  `Results[].Vulnerabilities[]`, is defensive against malformed input and clean
  scans (which omit the `Results` key), and returns `[]` rather than raising.
- **Issue model:** `TrivyIssue(BaseIssue)` carries `vuln_id`, `pkg_name`,
  `installed_version`, `fixed_version`, native `severity`, `title`, and
  `target`. The display message reads e.g. `Django 2.2.0: <title> (fixed in
  1.11.23, 2.1.11, 2.2.4)`; `PrimaryURL` becomes the issue `doc_url`.

### Severity behavior

Native `CRITICAL`/`HIGH`/`MEDIUM`/`LOW`/`UNKNOWN` values are honored via lintro's
severity alias table (`CRITICAL`/`HIGH` → ERROR, `MEDIUM`/`UNKNOWN` → WARNING,
`LOW` → INFO). The raw Trivy severity is retained on the issue so the exact tier
appears in output even though lintro's severity column normalizes to three
levels.

## Installation

Trivy is a single Go binary:

```bash
brew install trivy
# or download from https://github.com/aquasecurity/trivy/releases
```

`scripts/utils/install-tools.sh` downloads the pinned release tarball, verifies
its SHA-256 against the published `checksums.txt`, and installs the binary. The
pinned version lives in `lintro/_tool_versions.py` (`ToolName.TRIVY`) and is kept
current by a Renovate custom manager against the `aquasecurity/trivy` GitHub
releases; the manifest version follows via the tool-versions generator.

## Limitations

- **No autofix.** Trivy reports vulnerable pins and the version(s) that resolve
  them; `fix()` raises `NotImplementedError`. Remediation is a dependency
  upgrade.
- **Requires a local vulnerability database** (see
  [Vulnerability database / hermeticity](#vulnerability-database--hermeticity)).
- **Dependency-scoped** by design: secrets stay with gitleaks/trufflehog and IaC
  misconfiguration with checkov/hadolint (see
  [Scope decision](#scope-decision-dependency-vulnerability-scanning-only)).
