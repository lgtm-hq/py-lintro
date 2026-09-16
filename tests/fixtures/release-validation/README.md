# Release validation fixtures

One JSON file per scenario of the release validation runbook
(`docs/release-validation.md`, #2633): `S1.json`, `S2.json`, `S3.json`, `S4.json`. Each
is a record of a real tag run, written by hand from `gh run view`, the registries and
the issue tracker after the scenario ran. `tests/unit/test_release_validation.py`
asserts the scenario invariants over whatever files exist here and skips, with a reason,
while a scenario has no file yet, so the scenario PR only adds JSON.

The file name stem must equal the `scenario` field. Every key below is required unless
marked optional; unknown keys are rejected so a typo cannot pass as a missing check.

```json
{
  "scenario": "S1",
  "tag": "v0.160.3rc1",
  "version": "0.160.3rc1",
  "npm_version": "0.160.3-rc.1",
  "run": {
    "id": 12345678901,
    "url": "https://github.com/lgtm-hq/py-lintro/actions/runs/12345678901",
    "attempt": 1,
    "conclusion": "success"
  },
  "switches": {
    "RELEASE_VALIDATION_CHANNELS": "true",
    "RELEASE_FAULT": ""
  },
  "jobs": {
    "classify-tag": "success",
    "sbom": "success",
    "pypi-build": "success",
    "build-binaries": "success",
    "docker-build": "success",
    "release-gate": "success",
    "pypi-upload": "success",
    "github-release": "success",
    "mirror-token": "skipped",
    "mirror-release": "skipped",
    "homebrew-tap": "skipped",
    "npm-publish": "success",
    "docker-promote": "success",
    "notify-failure": "success"
  },
  "published": {
    "pypi": {
      "lintro-0.160.3rc1.tar.gz": "<sha256>",
      "lintro-0.160.3rc1-py3-none-any.whl": "<sha256>"
    },
    "github_release": {
      "prerelease": true,
      "assets": {
        "lintro-0.160.3rc1.tar.gz": "<sha256>",
        "lintro-0.160.3rc1-py3-none-any.whl": "<sha256>",
        "lintro-macos-arm64": "<sha256>",
        "lintro-linux-arm64": "<sha256>",
        "lintro-linux-x64": "<sha256>",
        "lintro.1": "<sha256>"
      }
    },
    "docker": {
      "ghcr.io/lgtm-hq/py-lintro-base": "sha256:<digest>",
      "ghcr.io/lgtm-hq/py-lintro": "sha256:<digest>",
      "ghcr.io/lgtm-hq/py-lintro-ai": "sha256:<digest>"
    },
    "npm": {
      "@lgtm-hq/lintro": { "version": "0.160.3-rc.1", "dist_tag": "next" },
      "@lgtm-hq/lintro-darwin-arm64": {
        "version": "0.160.3-rc.1",
        "dist_tag": "next",
        "binary_sha256": "<sha256 of bin/lintro inside the package>"
      },
      "@lgtm-hq/lintro-linux-arm64": { "...": "..." },
      "@lgtm-hq/lintro-linux-x64": { "...": "..." }
    },
    "homebrew": null
  },
  "issues": {
    "release_failure": null,
    "closing_comment": null
  },
  "wall_clock_seconds": 1234,
  "verification": { "<command>": "<output>" },
  "recovery": null,
  "notes": "free text"
}
```

Field notes:

- `run.conclusion` and every `jobs` value are GitHub's conclusions: `success`,
  `failure`, `skipped`, `cancelled`. `jobs` lists every job of `publish-pypi-on-tag.yml`
  by job id, as `gh run view <id> --json jobs` reports them (the job **id**, not the
  display name).
- `published.pypi`, `.github_release`, `.docker`, `.npm`: `null` (or `{}`) when the
  channel received nothing. `pypi` maps file name to sha256 from the PyPI JSON API;
  `github_release.assets` maps asset name to the sha256 from the release's `SHA256SUMS`;
  `docker` maps image to the index digest the version tag resolves to; `npm` maps
  package to `version`, `dist_tag` and, for the platform packages, `binary_sha256`.
  `homebrew` is always `null` (no prerelease lane).
- `issues.release_failure`: `null`, or `{"url": "...", "state": "open" | "closed"}` for
  the `release-failure:publish-pypi-on-tag:<tag>` issue as it stood when the scenario
  was recorded (S3 records it open; S4, recorded after the owner closed it, records it
  closed and adds `closed_by` and `closed_at`, the only two optional keys of that
  object). `issues.closing_comment`: the URL of the recovery summary comment (S4 only):
  the live run's closing summary, or the dry-run summary when the recovery was refused;
  else `null`.
- `attempts`: optional; earlier attempts or superseded candidates whose run ids would
  otherwise go unrecorded, each `{"run_id", "attempt", "conclusion", "note"}`. S1 lists
  the rc1 attempts it superseded; S2 lists the infra-flake attempt the auto-rerun re-ran
  before the recorded one.
- `wall_clock_seconds`: S1 only (tag push to last job); `null` elsewhere.
- `verification`: optional; S1's copied command outputs.
- `recovery`: S4 only, else `null`:

  ```json
  {
    "source_run_id": 12345678901,
    "dry_run": {
      "id": 1,
      "url": "...",
      "conclusion": "failure",
      "channels": [],
      "refusal": "ERROR: refusing to recover prerelease tag ...",
      "policy": "lgtm-ci#962 prerelease exemption (docs/release-recovery.md, Which tier applies)"
    },
    "live": null
  }
  ```

  `dry_run.conclusion` is the dry run's conclusion; `channels` is what it listed to
  resume (`[]` when detection never ran); `refusal` and `policy` are present only when
  the run refused: the verbatim message and the rule it applied. `live` is the live run
  (`{"id", "url", "conclusion"}`) or `null` when no live run was dispatched, which is
  the recorded outcome for a prerelease.

- `notes`: free text; S4's must state that prereleases are exempt from recovery by
  policy and that the scenario exercises the tooling on an `rc` deliberately.

The invariants the test holds each scenario to are the ones in the runbook: S1 green
with every channel but Homebrew present; S2 nothing published and no open issue; S3
PyPI, release and Docker present, npm absent, the issue filed, one attempt; S4 the
recovery dry run refused by the prerelease exemption (the rule named in
`recovery.dry_run.policy`): no channel resumed, no live run, npm still empty, the issue
closed by the owner citing the exemption, every digest equal to S3's.
