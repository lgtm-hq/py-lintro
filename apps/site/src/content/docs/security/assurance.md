---
title: 'assurance'
description:
  'This assurance note explains how `py-lintro` meets its documented security
  requirements and provides pointers to evidence.'
category: security
order: 10
navGroup: policy
---

# Security Assurance (Overview)

This assurance note explains how `py-lintro` meets its documented security requirements
and provides pointers to evidence.

## Requirements Coverage

- Governance and Roles — see `GOVERNANCE.md`
- Contribution Integrity — DCO sign-offs required; see `DCO.md` and CI checks
- Responsible Disclosure — see `SECURITY.md`
- Branch Protection — enforced via Allstar (`.allstar/branch_protection.yaml`)
- Dependency Hygiene — Renovate, Dependency Review CI, `uv.lock`
- Static/SAST — Ruff, Bandit, CodeQL; policy checks for workflows (Actionlint)
- Container and Dockerfile — Hadolint
- SBOM (Software Bill of Materials) — Automated generation in CycloneDX 1.6 and SPDX 2.3
  formats
  - Workflow: `.github/workflows/sbom-on-main.yml`
  - Generated on every push to main branch
  - Includes all Python, npm, and GitHub Actions dependencies
  - Artifacts available via GitHub Actions (90-day retention)
  - Meets Executive Order 14028 federal software requirements
- Documentation and Versioning — `README.md`, `CHANGELOG.md`, semantic releases

## Evidence Pointers

- CI Workflows: `.github/workflows/*.yml`
- Allstar Policy: `.allstar/branch_protection.yaml`
- Scorecard: badge in `README.md`
- Coverage: `coverage.xml` and README badge

## SBOM (Supply Chain Transparency)

py-lintro automatically generates Software Bill of Materials (SBOM) for supply chain
security:

### What is Generated

- **CycloneDX 1.6 JSON**: Industry-standard format for dependency tracking
- **SPDX 2.3 JSON**: Linux Foundation standard for license compliance
- **Complete inventory**: All direct and transitive dependencies
- **Cryptographic hashes**: For verification and integrity checking

### How to Access

1. **Via GitHub Actions UI**:
   - Navigate to
     [SBOM workflow runs](https://github.com/lgtm-hq/py-lintro/actions/workflows/sbom-on-main.yml)
   - Select the latest successful run
   - Download "sbom-artifacts" from the Artifacts section

2. **Via GitHub CLI**:

   ```bash
   gh run download -R lgtm-hq/py-lintro --name sbom-artifacts
   ```

3. **Via API**:

   ```bash
   gh api repos/lgtm-hq/py-lintro/actions/artifacts \
     --jq '.artifacts[] | select(.name=="sbom-artifacts") | .archive_download_url'
   ```

### Use Cases

- **Security Scanning**: Feed into vulnerability scanners (Grype, Snyk, etc.)
- **License Compliance**: Audit all dependency licenses
- **Supply Chain Security**: Track all software components
- **Federal Compliance**: Meets EO 14028 requirements
- **Enterprise Procurement**: Satisfy security questionnaires

### File Naming Convention

SBOM files are named: `{branch}-{commit-sha}-py-lintro-sbom.{format}.json`

Example: `main-3b97378f3438-py-lintro-sbom.cyclonedx-1.6.json`

This ensures traceability to exact codebase versions.

## Continuous Improvement

- Periodic updates via Renovate
- Automated analyses (Scorecard, CodeQL)
- Maintainer reviews for all changes
