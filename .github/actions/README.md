# Composite Actions

Centralized, reusable steps to keep workflows small and DRY. Each action exposes clear
inputs in its action.yml.

## Versioning Strategy

**Local composite actions** in this repository (referenced as
`uses: ./.github/actions/<name>`) ship with the py-lintro repo. They are not tagged or
versioned separately. When you change a local action, update the workflows that call it
in the same commit/PR — there is no `@actions-v*` pin to bump.

**External actions** (Marketplace, other repos, or lgtm-ci) must stay pinned to commit
SHAs in workflow files, per org policy. Breaking changes to those actions are absorbed
by repinning the SHA in the caller workflow.

## .github/actions/setup-docker

- Purpose: Set up Docker Buildx; optionally log in to a registry (e.g., GHCR)
- **Note**: This action does NOT include hardened runner - use in workflows that already
  have hardening
- Inputs:
  - login (string, default 'false'): set to 'true' to enable login
  - registry (string, default ghcr.io)
  - username (string)
  - password (string)
  - driver (string, default 'docker'): buildx driver

Example:

```yaml
- name: Setup Docker (Buildx + login)
  uses: ./.github/actions/setup-docker
  with:
    login: 'true'
    registry: ghcr.io
    username: ${{ github.actor }}
    password: ${{ secrets.GITHUB_TOKEN }}
```

## .github/actions/extract-version

- Purpose: Extract version from git tag reference, stripping the 'v' prefix
- Inputs:
  - strip-prefix (string, default 'true'): whether to strip the 'v' prefix
- Outputs:
  - version: the extracted version (without 'v' prefix if strip-prefix is true)
  - tag: the original tag name

Example:

```yaml
- name: Extract version from tag
  id: version
  uses: ./.github/actions/extract-version

- name: Use version
  run: echo "Version is ${{ steps.version.outputs.version }}"
```

## .github/actions/harden-runner-preset

- Purpose: Apply step-security/harden-runner with predefined endpoint presets
- Note: This action requires checkout first, so it cannot be the first step. For maximum
  security, use direct harden-runner calls as the first step.
- Inputs:
  - preset (string, required): endpoint preset to use
    - python: PyPI and GitHub
    - docker: Python + container registries
    - codecov: Python + Codecov
    - sigstore: Python + Sigstore services
    - homebrew: Python + Homebrew tap
    - full: All common endpoints
  - policy (string, default 'block'): egress policy (block or audit)
  - extra-endpoints (string, optional): additional endpoints to allow

Example:

```yaml
- name: Checkout
  uses: actions/checkout@v4

- name: Harden Runner
  uses: ./.github/actions/harden-runner-preset
  with:
    preset: python
    extra-endpoints: 'custom.example.com:443'
```

Notes:

- Workflows must remain in .github/workflows/ (no subdirectories supported).
- Prefer scripts under scripts/ for any logic beyond orchestration.
