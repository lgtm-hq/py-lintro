# npm Distribution

Lintro is distributed to JS/TS developers as an npm package built from
platform-specific, self-contained Nuitka binaries. This mirrors the proven model used by
esbuild, Biome, Turbo, and SWC: a small meta-package selects and launches the correct
native binary at runtime, so consumers need no Python.

## Package layout

The `npm/` directory holds five packages:

| Package  | npm name                       | Contents                                       |
| -------- | ------------------------------ | ---------------------------------------------- |
| Meta     | `@lgtm-hq/lintro`              | `bin/lintro` launcher + `optionalDependencies` |
| Platform | `@lgtm-hq/lintro-darwin-arm64` | macOS Apple Silicon binary                     |
| Platform | `@lgtm-hq/lintro-darwin-x64`   | macOS Intel binary                             |
| Platform | `@lgtm-hq/lintro-linux-arm64`  | Linux ARM64 binary                             |
| Platform | `@lgtm-hq/lintro-linux-x64`    | Linux x86_64 binary                            |

Each platform package declares `os` and `cpu` fields, so npm and bun download only the
binary matching the host. The meta-package lists all four as `optionalDependencies`; the
unsupported ones are skipped during install.

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
artifacts alongside the macOS ones.

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
repo, the `publish-npm.yml` workflow, and the `npm` environment. That `npm` environment
gates every publish behind maintainer approval, mirroring the `pypi` environment; npm
generates provenance attestations automatically. A manual `workflow_dispatch` defaults
to `dry_run: true` for safe testing.

The workflow accepts a `dist_tag` input (default `latest`). When backfilling a version
older than the registry's current `latest`, set `dist_tag` to a non-latest value such as
`backfill` — npm refuses to move `latest` backwards without an explicit `--tag`. The
version remains installable as `@lgtm-hq/lintro@<version>`; only the floating `latest`
pointer is left alone.

Trusted publishing requires **npm ≥ 11.5.1**. The workflow uses **Node 24**, which ships
a compatible bundled npm — do **not** run `npm install -g npm` (or any in-place
self-upgrade) in CI. That mutates the Actions toolcache npm tree and breaks
`npm publish --provenance` with `Cannot find module 'sigstore'`.

## Install context

`InstallContext.NPM_BIN` is detected when the running executable resolves under
`node_modules/@lgtm-hq/lintro-`, so install-aware commands can recognize npm-installed
binaries.
