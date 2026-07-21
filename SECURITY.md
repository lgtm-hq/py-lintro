# Security Policy

## Supported Versions

Currently supporting the latest stable version:

<!-- markdownlint-disable MD060 -- emoji triggers false positive -->

| Version | Supported |
| ------- | --------- |
| 0.91.x  | ✅        |
| < 0.91  | ❌        |

<!-- markdownlint-enable MD060 -->

## Reporting Security Vulnerabilities

Found a security vulnerability? Here's how to report it:

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

### **Vulnerability Disclosure Timeline**

We follow a coordinated vulnerability disclosure process:

- **Acknowledgment**: Within 48 hours of receiving your report, we will acknowledge
  receipt and begin our initial assessment.
- **Investigation**: Within 30 days, we will investigate the vulnerability, determine
  its severity, and develop a fix.
- **Fix & Release**: We aim to release a patch within 90 days of the initial report.
  Critical vulnerabilities may receive expedited fixes.
- **Public Disclosure**: After the fix is released, we will coordinate public disclosure
  with the reporter. We request that reporters refrain from public disclosure until a
  fix is available to protect users.
- **Updates**: You will be kept informed of progress throughout the process.

We appreciate responsible disclosure and will credit reporters (unless anonymity is
requested) in our security advisories and release notes.

## External Plugin Trust Model

Lintro supports third-party tool plugins discovered via Python entry points in the
`lintro.plugins` group. Loading such a plugin imports and executes its code, so Lintro
treats external plugins as untrusted by default.

### Threat model

Any package installed in the same environment as Lintro can expose a `lintro.plugins`
entry point. If Lintro loaded these unconditionally, a malicious or compromised
dependency would run arbitrary code every time the CLI starts — a supply-chain
escalation from "installed" to "executed on every invocation". Protocol validation does
not help here because it happens only _after_ the plugin module has already been
imported and executed.

### Default-deny, opt-in loading

- **External plugin loading is disabled by default.** A default installation never
  imports or executes third-party plugin code at startup.
- Enabling requires an explicit user action, via either mechanism below:
  - Set the environment variable `LINTRO_ENABLE_EXTERNAL_PLUGINS=1` (accepted truthy
    values: `1`, `true`, `yes`, `on`).
  - Configure a `plugins` section in `.lintro-config.yaml` (or `[tool.lintro.plugins]`
    in `pyproject.toml`) with `enabled: true` and/or a `trusted` allowlist.
- When a `trusted` allowlist is configured, only entry points whose entry-point name or
  distribution name appears in the list are loaded; every other discovered plugin is
  skipped and logged. Prefer the allowlist over the blanket env-var toggle so you only
  execute plugins you have explicitly vetted.

Only enable external plugins you trust and have reviewed. See
[docs/configuration.md](docs/configuration.md) for configuration details.

## For Contributors

- Review dependencies regularly for known vulnerabilities
- Test security-related changes thoroughly
- Never commit sensitive data (API keys, passwords, tokens, etc.)
- Follow secure coding practices
- Report any suspected vulnerabilities through the channels above

## Security Updates

Security fixes will be released as patch versions and documented in
[CHANGELOG.md](CHANGELOG.md). Users are encouraged to subscribe to GitHub release
notifications to stay informed about security updates.

## Contact

- **Primary**:
  [GitHub Security Advisories](https://github.com/lgtm-hq/py-lintro/security/advisories/new)
- **Email**: `turbocoder13@gmail.com`

Thanks for helping keep Lintro secure!
