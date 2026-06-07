---
title: 'requirements'
description:
  'This document summarizes security requirements adopted by `py-lintro` to support
  OpenSSF Best Practices (Silver).'
category: security
order: 20
navGroup: policy
---

# Security Requirements

This document summarizes security requirements adopted by `py-lintro` to support OpenSSF
Best Practices (Silver).

## Project and Process

- FLOSS license (MIT) and public repository
- Governance documented in `GOVERNANCE.md`
- Responsible disclosure process in `SECURITY.md`
- DCO required for contributions (`DCO.md`), enforced via commit sign-offs

## Source Integrity and Branch Protection

- All changes land via PR with maintainer review
- Branch protection enforced on `main` (no force pushes; required reviews; admins
  included)

## Dependencies and Supply Chain

- Dependencies managed via `pyproject.toml` and `uv.lock`
- Automated dependency updates (Renovate) with CI validation
- Dependency Review workflow enabled in CI

## Static and Policy Analysis

- Linting/formatting: Ruff, Black, Prettier, Yamllint, Hadolint, Actionlint
- Security linting: Bandit; GitHub CodeQL configured
- OpenSSF Scorecard monitored

## Build and Release

- CI builds, tests, and coverage for PRs and `main`
- Semantic versioning; signed release artifacts published via CI using OIDC

## Testing and Coverage

- Automated test suite with ~84% statement coverage
- New code requires tests and coverage reporting

## Secure Defaults

- No execution of untrusted code
- Docker image and scripts follow hardening practices (pinned actions, validated inputs)
