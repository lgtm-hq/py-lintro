# Security Policy

## Supported Versions

Currently supporting the latest stable version:

<!-- markdownlint-disable MD060 -- emoji triggers false positive -->

| Version | Supported |
| ------- | --------- |
| 0.80.x  | ✅        |
| < 0.80  | ❌        |

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
