# npm Distribution

Lintro is distributed to JS/TS developers as an npm package built from
platform-specific, self-contained Nuitka binaries. This mirrors the proven model used by
esbuild, Biome, Turbo, and SWC: a small meta-package selects and launches the correct
native binary at runtime, so consumers need no Python.

## Package layout

The `npm/` directory holds four packages:

| Package  | npm name                       | Contents                                       |
| -------- | ------------------------------ | ---------------------------------------------- |
| Meta     | `@lgtm-hq/lintro`              | `bin/lintro` launcher + `optionalDependencies` |
| Platform | `@lgtm-hq/lintro-darwin-arm64` | macOS Apple Silicon binary                     |
| Platform | `@lgtm-hq/lintro-linux-arm64`  | Linux ARM64 binary                             |
| Platform | `@lgtm-hq/lintro-linux-x64`    | Linux x86_64 binary                            |

Each platform package declares `os` and `cpu` fields, so npm and bun download only the
binary matching the host. The meta-package lists all three as `optionalDependencies`;
the unsupported ones are skipped during install.

There is no Intel macOS package (#2579). The MCP bundle makes `cryptography` a hard
dependency of the binary, and cryptography ships no Intel macOS wheel from 49.0.0 on, so
the x86_64 binary was dropped rather than built from source on a runner GitHub is
retiring. On an Intel Mac the launcher exits with a one-line pointer to the Homebrew
formula (`brew tap lgtm-hq/tap && brew install lintro`, which installs from PyPI there)
or to `pip install lintro`.

## Runtime resolution

`npm/lintro/bin/lintro` calls `resolveBinary()` in `npm/lintro/lib/resolve.js`, which
maps `${process.platform}-${process.arch}` to the scoped package, requires it, and reads
the exported binary `path`. The launcher then `execFileSync`s the binary, forwarding
argv and stdio and propagating its exit code. Resolution is pure and unit-tested
(`npm/lintro/test/resolve.test.js`, run with `bun test`).

## Build

`scripts/build/build_linux.py` compiles the Linux binary with Nuitka, mirroring the
existing `build_macos.py`. Linux has no cross-arch flag, so arm64 and x86_64 are built
natively on their respective runners. The `build-linux` job in
`.github/workflows/build-binary.yml` uploads `lintro-linux-x64` and `lintro-linux-arm64`
artifacts alongside the macOS arm64 one.

## Versioning

In-repo manifests carry a `0.0.0-dev` placeholder. At publish time,
`scripts/ci/npm/sync_npm_version.py --version <tag>` injects the release version into
every manifest and into the meta-package's `@lgtm-hq/lintro-*` pins. The same script's
`--check` mode guards internal consistency in CI.

## Publishing

`.github/workflows/publish-npm.yml` downloads the release binaries, stages them into the
npm tree, injects the version, runs a smoke test, and calls
`scripts/ci/npm/publish_packages.sh` with `LIVE=1`. It is invoked from the tag pipeline
(`publish-pypi-on-tag.yml`) after `build-binary.yml` uploads the platform binaries to
the GitHub release.

Publishing uses npm **trusted publishing (OIDC)**: no `NODE_AUTH_TOKEN` secret is
required. Each package on npmjs is configured with a trusted publisher pointing at this
repo and the `npm` environment, which gates every publish behind maintainer approval,
mirroring the `pypi` environment; npm generates provenance attestations automatically.

### Which runs npm actually trusts

The OIDC token npm receives identifies the **entry workflow of the run**, not the
reusable workflow doing the publishing. The trusted publisher configured for the
`@lgtm-hq/lintro*` packages names one workflow **file** — `publish-pypi-on-tag.yml` —
and the ref is not part of the match:

| Entry path                                             | OIDC identity (`github.workflow_ref`) | Live publish  |
| ------------------------------------------------------ | ------------------------------------- | ------------- |
| Tag push → `publish-pypi-on-tag.yml` → `workflow_call` | `publish-pypi-on-tag.yml @ <ref>`     | authenticates |
| **Run workflow** on `Publish - npm`                    | `publish-npm.yml @ <ref>`             | rejected      |

In practice the trusted entry path is a tag push, so its ref is typically `refs/tags/v*`
— but that is the usual example, not the identity: what npm checks is the workflow file
the run entered through.

Before #2247, a direct `workflow_dispatch` of `Publish - npm` therefore reached the
registry and failed there: the OIDC exchange fails, npm falls back to an unauthenticated
`PUT`, and the registry masks the authorization failure as `npm error code E404` ("could
not be found or you do not have permission to access it"). Provenance signing to
Sigstore still succeeds, which makes the log look like the publish almost worked. See
issue #2247 for two live dispatches that died that way, three retries deep and after
burning an `npm` environment approval. Such a run now fails in the `guard` job instead,
before any of that.

Two guards encode this:

- The `guard` job in `publish-npm.yml` (`scripts/ci/npm/assert_dispatch_allowed.sh`)
  allows a live publish only when the run's entry workflow is the tag pipeline, and
  fails every other run immediately. It carries no `environment:` and runs before the
  publish job, so a doomed run never consumes an `npm` approval. It decides on
  `github.workflow_ref` — the entry workflow, which is exactly what npm matches — not on
  `github.event_name`, which a `workflow_call` run inherits from its caller (so a
  dispatched `Publish - PyPI Production` run is still allowed to publish). Being an
  allowlist rather than a denylist on `publish-npm.yml`, an unknown caller, a renamed
  workflow, or a run with no identity to inspect all fail closed. Dispatching
  `Publish - npm` with `dry_run: true` — the dispatch default — stays supported for
  testing.
- `scripts/ci/npm/publish_packages.sh` classifies `E404` as a fatal auth failure, so a
  rejected publish is not retried three times per package.

The workflow accepts a `dist_tag` input (default `latest`). When backfilling a version
older than the registry's current `latest`, set `dist_tag` to a non-latest value such as
`backfill` — npm refuses to move `latest` backwards without an explicit `--tag`. The
version remains installable as `@lgtm-hq/lintro@<version>`; only the floating `latest`
pointer is left alone.

### Approval and backfills

The `npm` environment approval gate is intentionally preserved. Normal releases follow
the dependency order PyPI → platform binaries/Homebrew tap → npm: approve the npm
deployment only after the preceding jobs have uploaded the release binaries.

**The only supported way to publish or backfill a tag** is through that tag's
`Publish - PyPI Production` (`publish-pypi-on-tag.yml`) run — it is the entry path npm
trusts:

1. Open the run for the tag under Actions → `Publish - PyPI Production`.
2. If it already finished or failed, choose **Re-run failed jobs**; the rerun keeps the
   PyPI → binaries/Homebrew → npm order and the entry-path identity.
3. Approve the `npm` environment when that run reaches its waiting npm job.

Re-running a tag run is designed to be cheap (#2435): the Linux and macOS binary jobs
detect the verified binary already attached to the release (SHA256-matched against the
`sha256-*` artifact the same run produced) and skip the ~20-minute Nuitka rebuild, the
verify/smoke steps it feeds, and the release upload. Uploads also stage `<asset>.new`
and verify it before anything is removed, so the only moment the release lacks its
binary is the single delete-plus-rename API pair at the end; a kill there leaves
`<asset>.new` in place and the next attempt promotes it from the reuse check. So
**Re-run failed jobs** mostly re-drives the publish steps rather than repeating a build.

Do **not** dispatch `Publish - npm` live as a substitute — it cannot authenticate, and
the `guard` job now refuses it outright. A `dry_run: true` dispatch remains available
for exercising the packaging steps.

Renaming the tag pipeline means moving three things together: the workflow file itself,
`TRUSTED_ENTRY_WORKFLOW` in `scripts/ci/npm/assert_dispatch_allowed.sh`, and the trusted
publisher configured on npmjs (which lives outside this repo). The first two are pinned
to each other by `tests/unit/test_workflow_wiring.py`, which fails if the allowlisted
workflow is missing or no longer calls `publish-npm.yml`; the npmjs side is not, so
update it in the same change or every publish will start failing with `E404`.

Trusted publishing requires **npm ≥ 11.5.1**. The workflow uses **Node 24**, which ships
a compatible bundled npm — do **not** run `npm install -g npm` (or any in-place
self-upgrade) in CI. That mutates the Actions toolcache npm tree and breaks
`npm publish --provenance` with `Cannot find module 'sigstore'`.

## Install context

`InstallContext.NPM_BIN` is detected when the running executable resolves under
`node_modules/@lgtm-hq/lintro-`, so install-aware commands can recognize npm-installed
binaries.
