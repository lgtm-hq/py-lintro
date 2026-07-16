# pip-audit Test Samples

This directory contains sample requirements files for testing pip-audit
integration.

## Sample Files

- `pip_audit_violations.txt` — pins packages with historically known
  vulnerabilities (for manual/integration testing; requires network access).
- `pip_audit_clean.txt` — pins a package with no known vulnerabilities.

These follow the repo-wide `<tool>_<clean|violations>.<ext>` sample-naming
convention, so they intentionally do **not** match pip-audit's
`requirements*.txt` auto-discovery glob (which also keeps lintro's own
dogfooding from network-scanning them). Pass them explicitly to audit:

```bash
lintro check test_samples/tools/security/pip_audit/pip_audit_violations.txt \
  --tools pip_audit
```

## Why the Unit Tests Do Not Depend on the Network

pip-audit queries the PyPI Advisory Database and OSV over the network, so live
results are not stable enough to assert against. The unit tests therefore use:

- **Captured JSON output**: parser tests assert against JSON matching
  `pip-audit --format json`'s exact schema.
- **Mocked subprocess calls**: plugin tests mock the pip-audit command
  execution.

See:

- `tests/unit/parsers/test_pip_audit_parser.py`
- `tests/unit/tools/pip_audit/test_pip_audit_plugin.py`
