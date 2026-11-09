# Gitleaks image-build dependency closure

This module installs the unchanged canonical Gitleaks release source with a reviewed
dependency graph. `docker/tools.Dockerfile` checks this module's Gitleaks version
against `get_tool_version('gitleaks')` before installing anything; it builds with
`-mod=readonly`, the image's pinned Go compiler, `CGO_ENABLED=0`, and the upstream
`gore2regex` tag and Version ldflag. No source fork, replacement module, floating
build-time update is involved.

The eight changed embedded dependencies are:

| Module                            | Selected version                        |
| --------------------------------- | --------------------------------------- |
| `github.com/andybalholm/brotli`   | `v1.2.8`                                |
| `github.com/klauspost/compress`   | `v1.20.3`                               |
| `github.com/nwaples/rardecode/v2` | `v2.2.1`                                |
| `github.com/ulikunitz/xz`         | `v0.5.15`                               |
| `golang.org/x/crypto`             | `v0.58.1-0.20261009181718-2f1f834330b2` |
| `golang.org/x/sync`               | `v0.24.0`                               |
| `golang.org/x/sys`                | `v0.49.0`                               |
| `golang.org/x/text`               | `v0.43.0`                               |

RAR `v2.2.0` does not implement the `io.WriterTo` interface required by the unchanged
`mholt/archives v0.1.2`; `v2.2.1` restores compatibility. `brotli v1.2.8` fixes
`GO-2026-6888` (excessive allocation on crafted streams; `v1.2.8` also completes the
short-read detection of the `v1.2.7` fix). The `x/crypto` pseudo-version is `v0.58.0`
plus the single upstream commit that carves `openpgp` out into its own module, which is
the version OSV lists as fixing `GO-2026-5932`; no tagged release carries it yet. The
newer `x/crypto` requires `x/sys v0.49.0`, and `x/text` requires `x/sync v0.24.0`. Those
two transitive upgrades are not independent feature updates. The other 57 embedded
third-party dependency versions and checksums remain unchanged.

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

`GO-2026-5932` concerns `golang.org/x/crypto/openpgp`. The selected `x/crypto` no longer
contains that package (it is a separate module upstream), so the advisory is fixed in
this closure and the former time-limited root OSV exception is gone. Every image build
still checks the actual target-specific import closure with `verify_imports.py` before
installing Gitleaks and fails if an OpenPGP package is present, and a regression still
rejects additional non-fixture Go modules.

This closure is not whole-image security clearance. Other installed binaries,
pip-private dependency copies, distro packages, and the normal image
validation/publication/root-digest-adoption process remain separate gates.
