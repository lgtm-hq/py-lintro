# npm Distribution

Lintro is distributed to JS/TS developers as an npm package built from
platform-specific, self-contained Nuitka binaries. This mirrors the proven model used by
esbuild, Biome, Turbo, and SWC: a small meta-package selects and launches the correct
native binary at runtime, so consumers need no Python.

## Package layout

The `npm/` directory holds five packages:

| Package  | npm name                       | Contents                                       |
| -------- | ------------------------------ | ---------------------------------------------- |
| Meta     | `lintro`                       | `bin/lintro` launcher + `optionalDependencies` |
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

`.github/workflows/publish-npm.yml` is **dry-run only**. It downloads the release
binaries, stages them into the npm tree, injects the version, runs a smoke test, and
calls `scripts/ci/npm/publish_packages.sh`, which always passes `--dry-run` unless
`LIVE=1` is set. Going live is a deliberate follow-up that also requires a
`NODE_AUTH_TOKEN` secret and the `@lgtm-hq` npm org.

## Install context

`InstallContext.NPM_BIN` is detected when the running executable resolves under
`node_modules/@lgtm-hq/lintro-`, so install-aware commands can recognize npm-installed
binaries.
