# Release validation runbook

End-to-end failure and recovery validation of the tag pipeline on prerelease tags
(#2633, exit evidence for epic #2634). Four `rc` tags of one version, each driving one
scenario, prove that a green release produces every piece of evidence the
release-security policy (lgtm-hq/lgtm-ci#962) requires, that a forced build failure
publishes nothing, that a forced publish-stage failure files the release-failure issue,
and that the recovery tooling enforces the prerelease exemption (it refuses to resume a
prerelease; the live resume path is validated on the first stable recovery instead).

The scenario results are recorded as JSON under `tests/fixtures/release-validation/`
(schema in that directory's README) and asserted by
`tests/unit/test_release_validation.py`, so a later change to the pipeline's shape
breaks a test, not a release.

## The switches

Both switches are repository **variables** (Settings → Secrets and variables → Actions →
Variables), never secrets, so their state is visible in the run log. Both are read
exactly once, by the `classify-tag` job of `publish-pypi-on-tag.yml`, and travel as job
outputs; no other job reads `vars.*` (`tests/unit/test_workflow_wiring.py` pins this).
Both are **validation-only**: with the variables unset every gate behaves exactly as it
did before they existed, and a wiring test runs the classifier with them scrubbed to
prove it. Both are validated against an exact allowlist (`RELEASE_VALIDATION_CHANNELS`:
empty, `true`, `false`; `RELEASE_FAULT`: empty, `fail-build`, `fail-publish-npm`); any
other value fails `classify-tag` with a clear error and nothing is written.

| Variable                      | Effect                                                                                                                                                                                                                                                                                                                                                     |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RELEASE_VALIDATION_CHANNELS` | `true` opens the validation channels for a PEP 440 `X.Y.ZrcN` tag only (`classify-tag` output `validation_channels`): `npm-publish` runs under dist-tag `next`, `docker-promote` runs but retags `<version>` alone (no `latest`, `major.minor` or `major`), `homebrew-tap` stays skipped. An alpha, a beta or a stable tag ignores the variable entirely.  |
| `RELEASE_FAULT`               | `fail-build` or `fail-publish-npm` (`classify-tag` output `release_fault`), for an `rcN` tag only: any other tag gets an empty output by construction, so a leftover variable cannot touch a stable release. The named step exits 1 with an `::error` annotation; the other is untouched. Any other value fails `classify-tag` before anything is written. |

Where the faults live, and why:

- `fail-build` is a step in `release-gate`, right after the checkout and before any
  artifact download. The build calls (`pypi-build`, `build-binaries`, `docker-build`)
  are reusable workflows with no caller-side steps, so the gate, the last job before any
  irreversible write and the only build-side job in the file with steps, hosts the
  build-stage fault. A fired fault leaves the gate red with nothing verified, so
  `pypi-upload` and every publisher are skipped.
- `fail-publish-npm` is the first step of `publish-npm.yml`'s `stage` job after the
  runner hardening and the checkout, before any binary is downloaded. A called workflow
  cannot read `vars.*`, so the tag pipeline passes `release_fault` through as a
  `workflow_call` input. A fired fault leaves npm untouched while PyPI, the GitHub
  Release and the Docker promote complete: exactly the partial release the notifier and
  the recovery workflow exist for.

Both steps call `scripts/ci/release-fault.sh <name>`, which compares `RELEASE_FAULT`
against its argument and is a no-op otherwise
(`tests/bats/unit/ci/test_release_fault.bats`).

npm note: `package.json` requires SemVer, so `scripts/ci/npm/sync_npm_version.py` maps
the PEP 440 tag to its SemVer prerelease (`v0.160.3rc1` → `0.160.3-rc.1`); the registry
version of the validation packages is the mapped form.

## Before you start

- Every sibling of #2633 is merged and pinned: #2562, #2632, #2670, lgtm-hq/lgtm-ci#964
  and lgtm-hq/lgtm-ci#966, lgtm-hq/homebrew-tap#471.
- Four candidates were cut in order with the checkpoint procedure in
  `.github/workflows/README.md` (a version-bump PR titled
  `ci(release): checkpoint prerelease 0.160.3rcN (#2633)` touching only
  `pyproject.toml`, `lintro/__init__.py` and `uv.lock`, then a signed `v0.160.3rcN` tag
  on the squash-merge commit): `0.160.3rc1` (first S1 attempt, superseded after #2676),
  `0.160.3rc2` (S1), `0.160.3rc3` (S2) and `0.160.3rc4` (S3; S4 reuses it). One PR and
  one tag per proving scenario; a candidate that fails for a reason outside its scenario
  is re-cut, never retried by hand. The automatic re-runs the pre-0.74.2 auto-rerun made
  (rc1's second attempt; rc3's second, which is the recorded one) are not retries of a
  candidate: they are kept, with the superseded candidate's run ids, in the fixture's
  `attempts[]` so they stay meaningful.
- Approval gates still require a human on the `pypi` environment (`pypi-upload`) and the
  `npm` environment (the npm publish job) for each candidate that reaches them: budget
  two `pypi` approvals in total (S1 and S3; S2 fails before upload) and one `npm`
  approval (S1; S3's npm stage fails before its publish job); S4 never reaches an
  approval because the recovery refuses the prerelease.
- Set `RELEASE_VALIDATION_CHANNELS=true` before S1 and leave it set until cleanup.
  `RELEASE_FAULT` is unset before S1, set per scenario below, and deleted after S3.
- `cosign` 2.6 or newer for `--new-bundle-format`, `gh` with the attestation subcommand,
  `docker buildx`, `npm`.

Record, for each scenario, the run id and URL, the tag, the run's conclusion, the
per-job conclusions (`gh run view <id> --json jobs`), what was published and its
digests, and every issue or comment linked, in the JSON fixture for that scenario.

## S1 green

`RELEASE_VALIDATION_CHANNELS=true`, `RELEASE_FAULT` unset. Tag `v0.160.3rc2` (the rc1
attempt died on a promote allowlist bug, #2676, and was superseded; its two attempts are
recorded in S1's `attempts[]`).

Expected: the run is green; `sbom`, `pypi-build`, `build-binaries`, `docker-build`,
`release-gate`, `pypi-upload`, `github-release`, `docker-promote` and `npm-publish`
succeed; `homebrew-tap`, `mirror-token` and `mirror-release` are skipped;
`notify-failure` succeeds and files nothing. Note the wall clock from tag push to the
last job.

Verify every artifact class with the policy's commands (the same ones `docs/docker.md`,
`docs/npm-distribution.md` and `.github/SECURITY.md` carry):

```bash
TAG=v0.160.3rc2
VERSION=0.160.3rc2          # PyPI, GitHub Release assets
NPM_VERSION=0.160.3-rc.2    # npm (SemVer form of the same version)

# PyPI: the release exists and the files match the gate's SHA256SUMS
curl -fsS "https://pypi.org/pypi/lintro/${VERSION}/json" | jq -r '.urls[] | "\(.digests.sha256)  \(.filename)"'

# GitHub Release: prerelease flag, every asset, bundles next to each one
gh release view "$TAG" --repo lgtm-hq/py-lintro --json isPrerelease,assets \
  --jq '{prerelease: .isPrerelease, assets: [.assets[].name]}'
mkdir -p "release-${TAG}" && cd "release-${TAG}"
gh release download "$TAG" --repo lgtm-hq/py-lintro
sha256sum -c SHA256SUMS
gh attestation verify "lintro-${VERSION}.tar.gz" --repo lgtm-hq/py-lintro \
  --signer-workflow lgtm-hq/lgtm-ci/.github/workflows/reusable-build-python-dist.yml
gh attestation verify "lintro-${VERSION}-py3-none-any.whl" --repo lgtm-hq/py-lintro \
  --signer-workflow lgtm-hq/lgtm-ci/.github/workflows/reusable-build-python-dist.yml
for bin in lintro-macos-arm64 lintro-linux-arm64 lintro-linux-x64; do
  gh attestation verify "$bin" --repo lgtm-hq/py-lintro \
    --signer-workflow lgtm-hq/py-lintro/.github/workflows/build-binaries.yml
  gh attestation verify "$bin" --repo lgtm-hq/py-lintro --bundle "${bin}.intoto.jsonl"
done
cd ..

# Docker: <version> only, no latest moved, two signatures on each digest
for image in py-lintro-base py-lintro py-lintro-ai; do
  DIGEST=$(docker buildx imagetools inspect "ghcr.io/lgtm-hq/${image}:${VERSION}" \
    --format '{{ .Manifest.Digest }}')
  echo "${image} ${DIGEST}"
  gh attestation verify "oci://ghcr.io/lgtm-hq/${image}@${DIGEST}" \
    --repo lgtm-hq/py-lintro --signer-repo lgtm-hq/lgtm-ci
  cosign verify --new-bundle-format "ghcr.io/lgtm-hq/${image}@${DIGEST}" \
    --certificate-identity-regexp '^https://github\.com/lgtm-hq/(py-lintro/\.github/workflows/(docker-ci|publish-pypi-on-tag)\.yml|lgtm-ci/\.github/workflows/reusable-docker-(build|multiplatform)\.yml)@' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
  docker buildx imagetools inspect "ghcr.io/lgtm-hq/${image}@${DIGEST}" --format '{{ json .Provenance }}' | jq -e '. != null'
  docker buildx imagetools inspect "ghcr.io/lgtm-hq/${image}@${DIGEST}" --format '{{ json .SBOM }}' | jq -e '. != null'
done
# latest still resolves to the previous stable digest, not the candidate
docker buildx imagetools inspect ghcr.io/lgtm-hq/py-lintro:latest --format '{{ .Manifest.Digest }}'

# npm: four packages under `next`, latest untouched, attestations and signatures
for p in lintro lintro-darwin-arm64 lintro-linux-arm64 lintro-linux-x64; do
  npm view "@lgtm-hq/${p}@${NPM_VERSION}" dist.attestations dist.integrity
  npm dist-tag ls "@lgtm-hq/${p}"      # next -> NPM_VERSION, latest -> previous stable
done
mkdir -p /tmp/lintro-npm && cd /tmp/lintro-npm && npm init -y >/dev/null
npm install --ignore-scripts "@lgtm-hq/lintro@${NPM_VERSION}"
npm audit signatures
./node_modules/.bin/lintro --version
sha256sum node_modules/@lgtm-hq/lintro-linux-x64/bin/lintro   # equals the release's SHA256SUMS entry
gh attestation verify node_modules/@lgtm-hq/lintro-linux-x64/bin/lintro \
  --repo lgtm-hq/py-lintro \
  --signer-workflow lgtm-hq/py-lintro/.github/workflows/build-binaries.yml

# Homebrew: nothing, by design (no prerelease lane)
gh pr list --repo lgtm-hq/homebrew-tap --search "$VERSION" --state all
```

Copy each command's output into the S1 fixture (`verification` block) and the wall clock
into `wall_clock_seconds`.

## S2 build failure

`RELEASE_FAULT=fail-build`. Tag `v0.160.3rc3`.

Expected: `release-gate` fails at `Inject fault (fail-build)` with the
`::error title=Injected release fault::` annotation; `pypi-upload`, `github-release`,
`docker-promote`, `npm-publish`, `homebrew-tap` and the mirror lane are skipped; nothing
reaches any channel. `notify-failure` runs (the gate is a row in its table). By the
policy a pre-publish failure is not an incident: record whether the notifier filed
nothing, or filed and closed, and quote the policy text you matched it against (it filed
issue #2682 on attempt 2; attempt 1 was an infra flake re-run by the pre-0.74.2
auto-rerun, so the recorded attempt is 2 and attempt 1 sits in `attempts[]`).

```bash
VERSION=0.160.3rc3; NPM_VERSION=0.160.3-rc.3; TAG=v0.160.3rc3
curl -s -o /dev/null -w '%{http_code}\n' "https://pypi.org/pypi/lintro/${VERSION}/json"   # 404
npm view "@lgtm-hq/lintro@${NPM_VERSION}" version                                        # E404
docker buildx imagetools inspect "ghcr.io/lgtm-hq/py-lintro:${VERSION}"                    # not found
gh release view "$TAG" --repo lgtm-hq/py-lintro                                            # release not found
gh pr list --repo lgtm-hq/homebrew-tap --search "$VERSION" --state all                     # empty
gh issue list --repo lgtm-hq/py-lintro --search "release-failure:publish-pypi-on-tag:${TAG}" --state all
```

The staging images (`build-<run_id>` tags) may exist: they are not a release channel.

## S3 publish failure

`RELEASE_FAULT=fail-publish-npm`. Tag `v0.160.3rc4`.

Expected: PyPI, the GitHub Release and the Docker promote succeed; `npm-publish` fails
at `Inject fault (fail-publish-npm)` in the stage job, so nothing was packed, attested
or published on npm; the release-failure issue
`release-failure:publish-pypi-on-tag:v0.160.3rc4` exists (#2688) with the per-channel
table (npm failed, Homebrew and mirror skipped, everything else success). The injected
fault matches no infra signature, so `auto-rerun-on-infra-failure.yml` must not have
re-run the workflow: the run has exactly one attempt.

```bash
VERSION=0.160.3rc4; NPM_VERSION=0.160.3-rc.4; TAG=v0.160.3rc4
curl -fsS "https://pypi.org/pypi/lintro/${VERSION}/json" | jq -r '.urls[].filename'
gh release view "$TAG" --repo lgtm-hq/py-lintro --json isPrerelease,assets --jq '.assets[].name'
docker buildx imagetools inspect "ghcr.io/lgtm-hq/py-lintro:${VERSION}" --format '{{ .Manifest.Digest }}'
npm view "@lgtm-hq/lintro@${NPM_VERSION}" version                                        # E404
gh issue list --repo lgtm-hq/py-lintro --search "release-failure:publish-pypi-on-tag:${TAG}" --state open
gh run view <S3 run id> --repo lgtm-hq/py-lintro --json attempt --jq .attempt          # 1
```

Download the S3 release's `SHA256SUMS` and keep it: S4 compares against it. Record the
three image digests too.

## S4 recovery

Delete `RELEASE_FAULT` (leave `RELEASE_VALIDATION_CHANNELS=true`). No new tag: S4 runs
against S3's run (`v0.160.3rc4`, run 35003302274).

Prereleases are **exempt from recovery by policy** (lgtm-hq/lgtm-ci#962, "Which tier
applies" in lgtm-ci's release-recovery runbook: a candidate is never completed after the
fact). S4 exercises the recovery tooling on an `rc` deliberately. Because the exemption
is enforced by the tooling, the expected outcome is a **refusal**: the resolve job fails
with

```text
refusing to recover prerelease tag '<tag>': prereleases are abandoned by policy — cut a new prerelease version instead
```

No channel is resumed, and the dry-run summary is posted on the release-failure issue.
The live path is proven only on a stable tag. Say so in the fixture's `notes`, and
record the rule in `recovery.dry_run.policy`.

1. Dry run, from the default branch:

   ```bash
   gh workflow run release-recover.yml --repo lgtm-hq/py-lintro --ref main \
     -f tag=v0.160.3rc4 -f source-run-id=35003302274 -f dry-run=true
   ```

   Expected: `Resolve tag, artifacts, and channels` fails with the refusal above; the
   summary table shows resolve = failure and every resume skipped; the missing set at
   detection is empty because detection never ran. Record the dry-run run id.

2. No live run: the prerelease refusal makes a live dispatch pointless; the live path is
   validated on the first stable recovery instead.

```bash
NPM_VERSION=0.160.3-rc.4; VERSION=0.160.3rc4; TAG=v0.160.3rc4
# Nothing moved: npm still empty, asset and image digests equal S3's record
npm view "@lgtm-hq/lintro@${NPM_VERSION}" version                                        # E404
gh release download "$TAG" --repo lgtm-hq/py-lintro --pattern SHA256SUMS --output - | diff - /path/to/S3/SHA256SUMS
docker buildx imagetools inspect "ghcr.io/lgtm-hq/py-lintro:${VERSION}" --format '{{ .Manifest.Digest }}'
gh issue view 2688 --repo lgtm-hq/py-lintro --comments                                     # dry-run summary comment
```

Link S3's issue (#2688) and the dry-run summary comment in the S4 fixture.

## Cleanup

Leave the tags, the releases, the PyPI files and the image tags: all immutable by
policy. Deprecate the validation packages on npm and put the switches back:

```bash
# Only rc.2 reached npm: rc1's publish was cancelled at the gate, rc3 and rc4 never got there.
for v in 0.160.3-rc.2; do
  for p in lintro lintro-darwin-arm64 lintro-linux-arm64 lintro-linux-x64; do
    npm deprecate "@lgtm-hq/${p}@${v}" \
      "Release validation build (lgtm-hq/py-lintro#2633); not for use. Install the latest stable."
  done
done
# `next` must not keep pointing at a deprecated candidate
for p in lintro lintro-darwin-arm64 lintro-linux-arm64 lintro-linux-x64; do
  npm dist-tag rm "@lgtm-hq/${p}" next
done
gh variable delete RELEASE_VALIDATION_CHANNELS --repo lgtm-hq/py-lintro
gh variable delete RELEASE_FAULT --repo lgtm-hq/py-lintro   # if still present
```

Then record the five run ids (rc1 attempt, S1, S2, S3, S4 dry run) on #2633, commit the
four fixtures under `tests/fixtures/release-validation/`, and check the epic's exit
criteria off with this issue as the evidence.

## Checklist

- [ ] `RELEASE_VALIDATION_CHANNELS=true` set; `RELEASE_FAULT` unset.
- [x] S1 `v0.160.3rc2` green; every verify command above passed (`cosign verify` not run
      on the recording host); wall clock recorded (2887 s).
- [x] S2 `v0.160.3rc3` with `fail-build`: nothing on any channel; notifier behaviour
      matched against the policy text (#2682 filed and closed).
- [x] S3 `v0.160.3rc4` with `fail-publish-npm`: PyPI, release, Docker present; npm
      absent; issue filed with the channel table (#2688); one attempt only.
- [x] S4 dry run refused by the prerelease exemption; no live run; asset and image
      digests unchanged; issue closed by the owner citing the exemption.
- [x] npm packages deprecated, `next` removed, variables deleted.
- [x] Four fixtures committed; `tests/unit/test_release_validation.py` green.
- [x] Run ids, the S3 issue and the S4 dry-run summary linked on #2633; owner sign-off.

## Recorded runs

The scenario series ran on 2026-09-15. These are the runs the fixtures record and what
each proved, for #2634's exit checklist:

| Run                                                                          | Tag           | Scenario          | Outcome                                                                                                                            |
| ---------------------------------------------------------------------------- | ------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| [34967569536](https://github.com/lgtm-hq/py-lintro/actions/runs/34967569536) | `v0.160.3rc1` | S1, first attempt | `docker-promote` blocked on the attestation-store host (#2676); auto-rerun cancelled at the npm gate; superseded (S1 `attempts[]`) |
| [34986261473](https://github.com/lgtm-hq/py-lintro/actions/runs/34986261473) | `v0.160.3rc2` | S1                | Green end to end; every channel but Homebrew verified                                                                              |
| [34995758541](https://github.com/lgtm-hq/py-lintro/actions/runs/34995758541) | `v0.160.3rc3` | S2                | Gate failed on the injected build fault; nothing published; #2682 filed and closed                                                 |
| [35003302274](https://github.com/lgtm-hq/py-lintro/actions/runs/35003302274) | `v0.160.3rc4` | S3                | npm stage failed on the injected fault; PyPI, release and Docker present; #2688 filed                                              |
| [35007214074](https://github.com/lgtm-hq/py-lintro/actions/runs/35007214074) | `v0.160.3rc4` | S4                | Recovery dry run refused by the prerelease exemption; nothing moved                                                                |
