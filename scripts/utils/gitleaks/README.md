# Gitleaks image-build dependency closure

This module installs the unchanged canonical Gitleaks release source with a reviewed
dependency graph. `docker/tools.Dockerfile` checks this module's Gitleaks version
against `get_tool_version('gitleaks')` before installing anything; it builds with
`-mod=readonly`, the image's pinned Go compiler, `CGO_ENABLED=0`, and the upstream
`gore2regex` tag and Version ldflag. No source fork, replacement module, floating
build-time update is involved.

The seven changed embedded dependencies are:

| Module                            | Selected version |
| --------------------------------- | ---------------- |
| `github.com/klauspost/compress`   | `v1.18.7`        |
| `github.com/nwaples/rardecode/v2` | `v2.2.1`         |
| `github.com/ulikunitz/xz`         | `v0.5.15`        |
| `golang.org/x/crypto`             | `v0.56.0`        |
| `golang.org/x/sync`               | `v0.22.0`        |
| `golang.org/x/sys`                | `v0.47.0`        |
| `golang.org/x/text`               | `v0.41.0`        |

RAR `v2.2.0` does not implement the `io.WriterTo` interface required by the unchanged
`mholt/archives v0.1.2`; `v2.2.1` restores compatibility. The newer `x/crypto` requires
`x/sys v0.47.0`, and `x/text` requires `x/sync v0.22.0`. Those two transitive upgrades
are not independent feature updates. The other 58 embedded third-party dependency
versions and checksums remain unchanged.

## Updating

Use the Dockerfile's pinned Go version and a checksum-verifying module proxy. In this
directory, run `go get` with an explicit canonical Gitleaks version and explicit
versions for every security override. Commit both `go.mod` and `go.sum`, compare the
complete old/new executable buildinfo, and exercise real scanner/archive behavior plus
advisory coverage. Do not run `go mod tidy` here: there is deliberately no local Go
package importing the command. Historical checksums in `go.sum` do not imply those
versions are selected; the executable's buildinfo and `go list -m` identify the actual
graph.

A canonical Gitleaks bump intentionally fails the image build until this closure is
regenerated and reviewed. Do not remove the version check or make builds update the lock
automatically. Cache mounts keep Go source/build caches out of the image layers.

## Security scope

The module-level, no-fixed-version `GO-2026-5932` advisory concerns
`golang.org/x/crypto/openpgp`. The current `gore2regex`, CGO-disabled amd64 and arm64
command builds do not import any of its affected packages. Keep that narrow
package-absence evidence separate from saying the module is patched; recheck when
source, flags, platform or dependencies change. Every image build now checks the actual
target-specific import closure with `verify_imports.py` before installing Gitleaks and
fails if an affected OpenPGP package is present. A time-limited root OSV exception
expires on 2026-11-25; it records package absence, not a patched module. A regression
rejects additional non-fixture Go modules so that advisory-wide exception cannot
silently cover a new consumer. Reassess applicability and upstream status before expiry.

This closure is not whole-image security clearance. Other installed binaries,
pip-private dependency copies, distro packages, and the normal image
validation/publication/root-digest-adoption process remain separate gates.
