# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.167.x | :white_check_mark: |
| < 0.167 | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in lintro, please report it responsibly.

> **Do NOT open a public GitHub issue for security vulnerabilities.** All vulnerability
> reports must be submitted privately through one of the channels below.

### How to Report

Choose one of the following channels to disclose a vulnerability:

1. **GitHub Security Advisories** (preferred): Use
   [GitHub Security Advisories](https://github.com/lgtm-hq/py-lintro/security/advisories/new)
   to privately report and disclose the vulnerability
2. **Email**: Send a vulnerability report to `turbocoder13@gmail.com` with "SECURITY:
   Lintro" in the subject line

### What to Include

Regardless of the reporting channel, please include the following in your vulnerability
disclosure:

- Detailed steps to reproduce the vulnerability
- Affected version(s) and environment details
- Potential impact or severity assessment, if known
- Any suggested fixes or mitigations you might have

### Vulnerability Disclosure Timeline

- **Acknowledgment**: Within 48 hours of receiving your report
- **Investigation & Fix**: Within 30 days, we will assess severity and develop a fix
- **Release**: Critical vulnerabilities are patched as soon as possible; other fixes are
  released within 90 days
- **Public Disclosure**: Coordinated with the reporter after a fix is available

We appreciate responsible disclosure and will credit reporters (unless anonymity is
requested) in our security advisories and release notes.

## Repository Secrets

This project uses several GitHub repository secrets for CI/CD automation. Below is
documentation of their purposes and security considerations.

### Required Secrets

| Secret Name                   | Purpose                                             | Scope                                      | Rotation                  |
| ----------------------------- | --------------------------------------------------- | ------------------------------------------ | ------------------------- |
| `GITHUB_TOKEN`                | Built-in token for GitHub API access                | Automatic                                  | Per-workflow              |
| `MIRROR_REPO_TOKEN`           | Bump/tag the lintro-pre-commit mirror on release    | Contents + pull-requests write on mirror   | On compromise or key roll |
| `HOMEBREW_TAP_DISPATCH_TOKEN` | Trigger tap formula updates via repository_dispatch | Dispatch-only on `homebrew-tap` (no write) | On compromise or key roll |
| `CODECOV_TOKEN`               | Upload coverage reports                             | Codecov org                                | As needed                 |

### Optional Secrets

| Secret Name      | Purpose                                   | When Needed                  |
| ---------------- | ----------------------------------------- | ---------------------------- |
| `RELEASE_TOKEN`  | Create releases with elevated permissions | If GITHUB_TOKEN insufficient |
| `PYPI_API_TOKEN` | Manual PyPI publishing (backup)           | If OIDC fails                |

### Token Security Guidelines

1. **Principle of Least Privilege**: Each token should have only the minimum permissions
   required for its task.

2. **OIDC Preferred**: For PyPI publishing, we use OIDC (OpenID Connect) trusted
   publishing instead of API tokens. This eliminates long-lived credentials.

3. **Token Rotation Schedule**:
   - `HOMEBREW_TAP_DISPATCH_TOKEN`: Rotate if compromised; dispatch-only, cannot write
     to the tap
   - `CODECOV_TOKEN`: Rotate if Codecov reports suspicious activity
   - `GITHUB_TOKEN`: Automatic, no rotation needed

4. **Secret Scanning**: GitHub secret scanning is enabled to detect accidentally
   committed credentials.

5. **Retired App Credentials**: The organization-level GitHub App that formerly held
   direct write access to `homebrew-tap` has been decommissioned. Removing the former
   `HOMEBREW_TAP_APP_ID` / `HOMEBREW_TAP_APP_PRIVATE_KEY` entries from this document
   does not remove the App itself. An organization admin must verify that no other
   repository still depends on the App, then manually delete the former org-level
   `HOMEBREW_TAP_APP_ID` and `HOMEBREW_TAP_APP_PRIVATE_KEY` secrets and rotate (or
   delete) the underlying GitHub App private key.

## Workflow Security

### Harden Runner

All workflows use `step-security/harden-runner` with strict egress policies:

```yaml
- name: Harden Runner
  uses: step-security/harden-runner@<sha>
  with:
    egress-policy: 'block'
    allowed-endpoints: >
      github.com:443 api.github.com:443 pypi.org:443 pypi.python.org:443
      upload.pypi.org:443 files.pythonhosted.org:443
```

### Action Pinning

LGTM HQ policy ([lgtm-ci#221](https://github.com/lgtm-hq/lgtm-ci/pull/221),
[.github#12](https://github.com/lgtm-hq/.github/pull/12)): pin third-party and lgtm-ci
actions to **release commit SHAs** with a trailing Renovate version comment on the same
line. Tag refs are not allowed.

```yaml
# Good — SHA + version comment (Renovate-discoverable)
uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

# Bad — movable tag
uses: actions/checkout@v4

# Bad — bare SHA (Renovate disables; fails validate-action-pinning)
uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
```

The same rule applies to `tooling-ref:` and manual lgtm-ci checkout `ref:` fields.
Enforcement runs via `validate-action-pinning.yml` (lgtm-ci reusable workflow). Renovate
automation comes from the org preset (`extends: local>lgtm-hq/.github:renovate-config`).

### Permissions

Workflows follow the principle of least privilege:

```yaml
permissions:
  contents: read # Default: read-only

# Only escalate when necessary
permissions:
  contents: write # For releases
  id-token: write # For OIDC
```

## Permission Scopes by Workflow

| Workflow                   | Permissions                  | Justification         |
| -------------------------- | ---------------------------- | --------------------- |
| `docker-ci.yml`            | `contents: read` (+ per-job) | CI pipeline + quality |
| `test-ci.yml`              | `contents: read` (+ per-job) | Unit tests            |
| `publish-pypi-on-tag.yml`  | `{}` (+ per-job)             | Release + OIDC        |
| `build-binaries.yml`       | `{}` (+ per-job)             | Build + attest        |
| `publish-binaries.yml`     | `{}` (+ per-job)             | Homebrew dispatch     |
| `docker-build-publish.yml` | `contents: read` (+ per-job) | Push to GHCR          |

In the tag pipeline `contents: write` is held by the `github-release` call alone; the
build jobs hold `id-token: write` + `attestations: write` to attest what they built, and
`release-gate` and `pypi-upload` hold `attestations: read` to verify it.

## Supply Chain Security

### SBOM Generation

Software Bill of Materials (SBOM) is generated for each release using:

- `cyclonedx-bom` for Python dependencies
- A GitHub build-provenance attestation on every release asset — the sdist and wheel
  (attested inside lgtm-ci's `reusable-build-python-dist.yml`) and the three platform
  binaries (attested inside `build-binaries.yml`) — created **before** the first
  publish. The `release-gate` job of `publish-pypi-on-tag.yml` runs
  `gh attestation verify` on every one of them, checks `SHA256SUMS`, and only then does
  PyPI, the GitHub Release or any other channel receive anything; nothing is rebuilt
  after the `pypi` approval. The Sigstore bundles ship on the GitHub Release as
  `<asset>.intoto.jsonl` next to `SHA256SUMS`. A rerun never overwrites a published
  asset whose bytes differ (the reusable's `immutable-assets` guard); locking published
  releases at the API level is GitHub's repository-wide "Immutable releases" setting,
  which is enabled separately by the repository owner.
- BuildKit SBOM and provenance attestations on every published container image
  (`py-lintro`, `py-lintro-base`, `py-lintro-ai` and the tools images), plus a GitHub
  build-provenance attestation and a Cosign keyless signature on each image digest. The
  release images are built, attested and signed under staging tags before the `pypi`
  approval, verified and signed again by `publish-pypi-on-tag.yml` at promote time, and
  promoted to their version tags by digest; the manual backfill and the `main` promotion
  produce the same evidence. See "Verify an image" and "Verify a release asset" in
  `docs/docker.md`.

### Dependency Updates

- Dependabot monitors for security updates
- Renovate handles routine dependency updates
- All updates require CI passing before merge

## Code Signing

PyPI packages are published using OIDC trusted publishing, which provides:

- Cryptographic proof of build provenance
- No long-lived credentials to leak
- Transparent build logs

Release assets on GitHub can be verified offline from the attached bundles or online
against the attestations API:

```bash
gh attestation verify lintro-linux-x64 --repo lgtm-hq/py-lintro \
  --signer-workflow lgtm-hq/py-lintro/.github/workflows/build-binaries.yml
gh attestation verify lintro-<version>-py3-none-any.whl --repo lgtm-hq/py-lintro \
  --signer-workflow lgtm-hq/lgtm-ci/.github/workflows/reusable-build-python-dist.yml
gh attestation verify lintro-linux-x64 --repo lgtm-hq/py-lintro \
  --bundle lintro-linux-x64.intoto.jsonl
sha256sum -c SHA256SUMS
```

## Incident Response

In case of a security incident:

1. **Immediately** revoke any compromised tokens
2. **Audit** recent workflow runs for suspicious activity
3. **Notify** users if their data may be affected
4. **Document** the incident and response
5. **Improve** controls to prevent recurrence
