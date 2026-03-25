# Security Policy

## Supported Versions

Currently supporting the latest stable version:

<!-- markdownlint-disable MD060 -- emoji triggers false positive -->

| Version | Supported |
| ------- | --------- |
| 0.64.x  | ✅        |
| < 0.64  | ❌        |

<!-- markdownlint-enable MD060 -->

## Reporting Security Vulnerabilities

Found a security vulnerability? Here's how to report it:

### **Private Reporting Only**

Please **do not** create public GitHub issues for security vulnerabilities. This helps
prevent potential exploitation while we work on a fix. Public disclosure of
vulnerabilities before a fix is available puts all users at risk.

### **How to Report**

1. **GitHub Security Advisories**: Use
   [GitHub Security Advisories](https://github.com/lgtm-hq/py-lintro/security/advisories/new)
   to privately disclose a vulnerability. This is the preferred reporting channel
   as it allows structured coordination and tracking.
2. **Email**: Send details to `turbocoder13@gmail.com`
3. **Subject**: Include "SECURITY: Lintro" in the subject line
4. **Details**: Provide a clear description of the vulnerability

### **What to Include**

- Description of the vulnerability and its potential impact
- Steps to reproduce (if possible)
- Affected versions and configurations
- Any suggested fixes or mitigations you might have

### **Vulnerability Disclosure Timeline**

We follow a coordinated vulnerability disclosure process:

- **Acknowledgment**: Within 48 hours of receiving your report, we will
  acknowledge receipt and begin our initial assessment.
- **Investigation**: Within 30 days, we will investigate the vulnerability,
  determine its severity, and develop a fix.
- **Fix & Release**: We aim to release a patch within 90 days of the initial
  report. Critical vulnerabilities may receive expedited fixes.
- **Public Disclosure**: After the fix is released, we will coordinate public
  disclosure with the reporter. We request that reporters refrain from public
  disclosure until a fix is available to protect users.
- **Updates**: You will be kept informed of progress throughout the process.

We appreciate responsible disclosure and will credit reporters (unless anonymity
is requested) in our security advisories and release notes.

## For Contributors

- Review dependencies regularly for known vulnerabilities
- Test security-related changes thoroughly
- Never commit sensitive data (API keys, passwords, tokens, etc.)
- Follow secure coding practices
- Report any suspected vulnerabilities through the channels above

## Security Updates

Security fixes will be released as patch versions and documented in
[CHANGELOG.md](CHANGELOG.md). Users are encouraged to subscribe to GitHub
release notifications to stay informed about security updates.

## Contact

- **Primary**: [GitHub Security Advisories](https://github.com/lgtm-hq/py-lintro/security/advisories/new)
- **Email**: `turbocoder13@gmail.com`

Thanks for helping keep Lintro secure!
