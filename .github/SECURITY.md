# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.22.x  | :white_check_mark: |
| < 0.22  | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in lintro, please report it by:

1. **Do NOT open a public GitHub issue**
2. Report via
   [GitHub's private vulnerability reporting](https://github.com/lgtm-hq/py-lintro/security/advisories/new)
3. Include detailed steps to reproduce the vulnerability
4. Allow reasonable time for the issue to be addressed before public disclosure

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

All third-party actions are pinned to full commit SHAs, not version tags:

```yaml
# Good - pinned to SHA
uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683

# Bad - version tag can be moved
uses: actions/checkout@v4
```

The `validate-action-pinning.yml` workflow enforces this policy.

### Permissions

Workflows follow the principle of least privilege:

```yaml
permissions:
  contents: read  # Default: read-only

# Only escalate when necessary
permissions:
  contents: write  # For releases
  id-token: write  # For OIDC
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
