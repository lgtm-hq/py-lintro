# pip-audit Test Samples

This directory contains sample requirements files for testing pip-audit
integration.

## Sample Files

- `requirements_violations.txt` — pins packages with historically known
  vulnerabilities (for manual/integration testing; requires network access).
- `requirements_clean.txt` — pins a package with no known vulnerabilities.

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
