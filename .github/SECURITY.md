# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.86.x  | :white_check_mark: |
| < 0.86  | :x:                |

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

| Secret Name                    | Purpose                                           | Scope                               | Rotation                  |
| ------------------------------ | ------------------------------------------------- | ----------------------------------- | ------------------------- |
| `GITHUB_TOKEN`                 | Built-in token for GitHub API access              | Automatic                           | Per-workflow              |
| `HOMEBREW_TAP_APP_ID`          | GitHub App ID for homebrew-tap formula automation | App registered under org            | If app reset              |
| `HOMEBREW_TAP_APP_PRIVATE_KEY` | PEM for Homebrew tap GitHub App (see workflow)    | `contents: write` on `homebrew-tap` | On compromise or key roll |
| `CODECOV_TOKEN`                | Upload coverage reports                           | Codecov org                         | As needed                 |

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
   - `HOMEBREW_TAP_APP_PRIVATE_KEY`: Rotate if compromised; regenerate in App settings
   - `CODECOV_TOKEN`: Rotate if Codecov reports suspicious activity
   - `GITHUB_TOKEN`: Automatic, no rotation needed

4. **Secret Scanning**: GitHub secret scanning is enabled to detect accidentally
   committed credentials.

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

| Workflow                   | Permissions                        | Justification         |
| -------------------------- | ---------------------------------- | --------------------- |
| `docker-ci.yml`            | `contents: read` (+ per-job)       | CI pipeline + quality |
| `test-ci.yml`              | `contents: read` (+ per-job)       | Unit tests            |
| `publish-pypi-on-tag.yml`  | `contents: write, id-token: write` | Release + OIDC        |
| `build-binary.yml`         | `contents: write`                  | Upload release assets |
| `docker-build-publish.yml` | `contents: read, packages: write`  | Push to GHCR          |

## Supply Chain Security

### SBOM Generation

Software Bill of Materials (SBOM) is generated for each release using:

- `cyclonedx-bom` for Python dependencies
- Attestation artifacts for verification

### Dependency Updates

- Dependabot monitors for security updates
- Renovate handles routine dependency updates
- All updates require CI passing before merge

## Code Signing

PyPI packages are published using OIDC trusted publishing, which provides:

- Cryptographic proof of build provenance
- No long-lived credentials to leak
- Transparent build logs

## Incident Response

In case of a security incident:

1. **Immediately** revoke any compromised tokens
2. **Audit** recent workflow runs for suspicious activity
3. **Notify** users if their data may be affected
4. **Document** the incident and response
5. **Improve** controls to prevent recurrence
