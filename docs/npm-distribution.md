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
or to `pip install lintro`. The `@lgtm-hq/lintro-darwin-x64` package that shipped up to
0.155.0 is orphaned and is to be deprecated on the registry by the owner
(`npm deprecate`, tracked on the owner list of #2632).

## Runtime resolution

`npm/lintro/bin/lintro` calls `resolveBinary()` in `npm/lintro/lib/resolve.js`, which
maps `${process.platform}-${process.arch}` to the scoped package, requires it, and reads
the exported binary `path`. The launcher then calls `ensureExecutable()` — the publish
pipeline hands the staged packages between jobs as a workflow artifact, and that zip
lands every file as mode 0644, so the published tarball may carry the binary without its
exec bit; npm restores modes only for a package's own `bin` entries, which the platform
packages do not declare — and `execFileSync`s the binary, forwarding argv and stdio and
propagating its exit code. Resolution and the mode repair are pure and unit-tested
(`npm/lintro/test/resolve.test.js`, run with `bun test`).

## Build

`scripts/build/build_linux.py` compiles the Linux binary with Nuitka, mirroring the
existing `build_macos.py`. Linux has no cross-arch flag, so arm64 and x86_64 are built
natively on their respective runners. The build stage
(`.github/workflows/build-binaries.yml`) uploads `lintro-linux-x64`,
`lintro-linux-arm64` and `lintro-macos-arm64` artifacts and attests each one; the
release gate verifies them and writes the `SHA256SUMS` the GitHub Release carries
(#2562).

## Versioning

In-repo manifests carry a `0.0.0-dev` placeholder. At publish time,
`scripts/ci/npm/sync_npm_version.py --version <tag>` injects the release version into
every manifest and into the meta-package's `@lgtm-hq/lintro-*` pins. The same script's
`--check` mode guards internal consistency in CI.

## Publishing

`.github/workflows/publish-npm.yml` runs two jobs. It is invoked from the tag pipeline
(`publish-pypi-on-tag.yml`) after the GitHub Release job has attached the gated assets;
stable releases only.

**`stage`** (this repository, `environment: npm`):

1. `download_release_binaries.sh` fetches the three binaries and the release's
   `SHA256SUMS`.
2. `verify_release_binaries.sh` checks every binary's sha256 against that manifest and
   runs
   `gh attestation verify --repo lgtm-hq/py-lintro --signer-workflow lgtm-hq/py-lintro/.github/workflows/build-binaries.yml`
   on it. A replaced release asset (the #2484 incident overwrote four by accident) or a
   binary without a build attestation fails here, before anything is staged.
3. `stage_binaries.py` copies them into `npm/<platform>/bin/lintro`;
   `sync_npm_version.py --version` / `--check` inject and verify the version.
4. `smoke_test.sh` packs the meta and host-platform packages, installs them into a
   scratch project and runs `lintro --version` — twice, the second time with the
   installed binary stripped to mode 0644 to prove the launcher's exec-bit repair.
5. `write_package_checksums.py` writes `npm/SHA256SUMS`: one line per file
   `npm pack --dry-run` would ship, for all four packages, relative to `npm/`.
6. `actions/attest-build-provenance` attests exactly those subjects
   (`subject-checksums: npm/SHA256SUMS`), signing as `publish-npm.yml`, and the whole
   `npm/` tree is uploaded as the `npm-dist` artifact.

**`publish`** — lgtm-ci's `reusable-publish-npm-set.yml`, pinned at the repo-wide
lgtm-ci SHA, with `packages-dir: npm`, `artifact-name: npm-dist`,
`order: ["darwin-arm64", "linux-arm64", "linux-x64", "lintro"]`,
`checksums-file: npm/SHA256SUMS`, `signer-repo: lgtm-hq/py-lintro`,
`signer-workflow: .github/workflows/publish-npm.yml`, `post-publish-verify: true` and
`smoke-command: ./node_modules/.bin/lintro --version`. Its steps, in a fixed order the
reusable's own tests assert:

1. **Entry guard**: on a live run the entry workflow must be
   `.github/workflows/publish-pypi-on-tag.yml` (see below).
2. **Verify artifacts**: for every file npm would pack in every package, sha256 against
   `npm/SHA256SUMS` plus `gh attestation verify` against the stage job's attestation. A
   packed file the manifest does not list, a digest mismatch or a missing attestation
   fails the job before the real `npm pack`.
3. **Publish set**: the only step that writes to the registry. Platform packages first,
   meta package last; `npm view` pre-check skips already-published versions;
   `EPUBLISHCONFLICT` is an idempotent success; bounded exponential backoff on transient
   Sigstore/5xx/429 errors only; auth failures (including npm's masked `E404`) are never
   retried.
4. **Verify published**: `npm view` per package must show `dist.attestations` and
   `dist.integrity` (bounded propagation retry); the meta package is installed from the
   registry into a scratch directory where `npm audit signatures` and the smoke command
   must pass.

Publishing uses npm **trusted publishing (OIDC)**: no `NODE_AUTH_TOKEN` secret is
required and npm generates provenance attestations automatically (`--provenance` is
passed as explicit intent). The `npm` environment gates every live publish behind
maintainer approval, mirroring the `pypi` environment. A job that calls a reusable
cannot declare `environment:`, so the approval sits on `stage`, which `publish` needs;
the publish job itself carries no environment, so the trusted publisher on npmjs must be
registered **without** an environment name.

### Which runs npm actually trusts

The OIDC token npm receives identifies the **entry workflow of the run**, not the
reusable workflow doing the publishing. The trusted publisher configured for the
`@lgtm-hq/lintro*` packages names one workflow **file** — `publish-pypi-on-tag.yml` —
and the ref is not part of the match:

| Entry path                                             | OIDC identity (`github.workflow_ref`) | Live publish  |
| ------------------------------------------------------ | ------------------------------------- | ------------- |
| Tag push → `publish-pypi-on-tag.yml` → `workflow_call` | `publish-pypi-on-tag.yml @ <ref>`     | authenticates |
| **Run workflow** on `Publish - npm`                    | `publish-npm.yml @ <ref>`             | rejected      |

Before #2247, a direct `workflow_dispatch` of `Publish - npm` reached the registry and
failed there: the OIDC exchange fails, npm falls back to an unauthenticated `PUT`, and
the registry masks the authorization failure as `npm error code E404`. The reusable's
entry guard now refuses such a live run before anything is packed: `publish-npm.yml`
passes `entry-workflows: .github/workflows/publish-pypi-on-tag.yml` whenever `dry_run`
is false, and `tests/unit/test_workflow_wiring.py` pins that name to a workflow that
exists and really calls `publish-npm.yml`. A `dry_run: true` dispatch — the dispatch
default — leaves the allowlist empty (the guard only warns) so the whole pipeline can be
rehearsed without publishing; the reusable never contacts the registry on a dry-run.
Note that a live dispatch of `Publish - npm` still consumes an `npm` approval on `stage`
before the guard refuses it: do not approve one.

The workflow accepts a `dist_tag` input (default `latest`). When backfilling a version
older than the registry's current `latest`, set `dist_tag` to a non-latest value such as
`backfill` — npm refuses to move `latest` backwards without an explicit `--tag`. The
version remains installable as `@lgtm-hq/lintro@<version>`; only the floating `latest`
pointer is left alone.

### Approval and backfills

Normal releases follow the dependency order release gate (everything built, attested and
verified) → PyPI → GitHub Release (dist, the three binaries, man page, `SHA256SUMS`,
Sigstore bundles) → npm; npm no longer waits on the Homebrew dispatch (#2562). Approve
the npm deployment only after the GitHub Release job has attached the binaries and
`SHA256SUMS` — `stage` fails without the manifest.

**The only supported way to publish or backfill a tag** is through that tag's
`Publish - PyPI Production` (`publish-pypi-on-tag.yml`) run — it is the entry path npm
trusts:

1. Open the run for the tag under Actions → `Publish - PyPI Production`.
2. If it already finished or failed, choose **Re-run failed jobs**; the rerun keeps the
   gate → PyPI → GitHub Release → npm order and the entry-path identity. A failed run
   also has a `release-failure:publish-pypi-on-tag:<tag>` issue with the per-channel
   table; check it before approving the rerun.
3. Approve the `npm` environment when that run reaches its waiting `stage` job.

Re-running a tag run is designed to be cheap (#2435): once the release exists, the Linux
and macOS binary jobs detect the verified binary already attached to it and skip the
Nuitka rebuild. The GitHub Release job never overwrites a published asset whose bytes
differ (`immutable-assets`), so a rerun cannot strip or replace a binary npm is about to
download — and if it somehow did, `verify_release_binaries.sh` would refuse it.

### Re-run behaviour

A re-run over already-published packages is read-first (#2631, carried into the reusable
unchanged): each package already on the registry is skipped, and its dist-tag is checked
with `npm dist-tag ls` — when the requested tag already points at that version nothing
is written, which matters because the publish-scoped OIDC token cannot run
`npm dist-tag add` at all (npm/cli#8547). If the tag points elsewhere (drift), the
reconcile write is attempted once, and when the registry refuses it the package gets a
`::warning::` naming the expected and actual tags while the remaining packages still
publish. The job exits non-zero only after every package has been processed and exposes
`dist-tag-drift: true`, so a half-published release completes first and any leftover
drift is repaired with classic auth:
`npm dist-tag add @lgtm-hq/<package>@<version> <tag>`. Post-publish verification still
runs on such a re-run, against whatever the registry now holds.

### Verifying a published version by hand

```bash
# Provenance and integrity per package (all four must show dist.attestations)
for p in lintro lintro-darwin-arm64 lintro-linux-arm64 lintro-linux-x64; do
  npm view "@lgtm-hq/${p}@<version>" dist.attestations dist.integrity
done

# Registry signatures + attestations for what a consumer actually installs
mkdir -p /tmp/lintro-npm && cd /tmp/lintro-npm && npm init -y >/dev/null
npm install --ignore-scripts "@lgtm-hq/lintro@<version>"
npm audit signatures
./node_modules/.bin/lintro --version

# The binary inside the platform package is the release asset the gate verified
sha256sum node_modules/@lgtm-hq/lintro-linux-x64/bin/lintro   # compare with the release's SHA256SUMS
gh attestation verify node_modules/@lgtm-hq/lintro-linux-x64/bin/lintro \
  --repo lgtm-hq/py-lintro \
  --signer-workflow lgtm-hq/py-lintro/.github/workflows/build-binaries.yml
```

Renaming the tag pipeline means moving three things together: the workflow file itself,
the `entry-workflows` value in `publish-npm.yml`, and the trusted publisher configured
on npmjs (which lives outside this repo). The first two are pinned to each other by
`tests/unit/test_workflow_wiring.py`, which fails if the allowlisted workflow is missing
or no longer calls `publish-npm.yml`; the npmjs side is not, so update it in the same
change or every publish will start failing with `E404`.

Trusted publishing requires **npm ≥ 11.5.1**. Both jobs use **Node 24**, which ships a
compatible bundled npm — do **not** run `npm install -g npm` (or any in-place
self-upgrade) in CI. That mutates the Actions toolcache npm tree and breaks
`npm publish --provenance` with `Cannot find module 'sigstore'`.

## Install context

`InstallContext.NPM_BIN` is detected when the running executable resolves under
`node_modules/@lgtm-hq/lintro-`, so install-aware commands can recognize npm-installed
binaries.
