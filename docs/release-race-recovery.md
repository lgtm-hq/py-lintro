# Release race recovery (#3054)

Queue merges stay paused until this recovery lands under normal required checks,
resolved review threads and local dual-review gates. This is a deterministic release
generation race, not runner loss. Do not retry publication or override its approvals.

## Evidence and cause

- Release PR [#3042](https://github.com/lgtm-hq/py-lintro/pull/3042) merged at
  `2026-10-31T10:02:27Z`, setting main's version to `0.173.4` at
  `627cb4c06dc22f97643aaf871559bf8b49b65464`.
- Version-PR run [41191066047](https://github.com/lgtm-hq/py-lintro/actions/runs/41191066047)
  was triggered earlier by `aca3ec105e723ac9f88a56e5378f1174a5d88d92`.
  Its gate passed at `10:02:09Z` against successful publication of `v0.173.3`,
  before #3042 merged. Blocking active publication at that early gate alone cannot
  prevent this interleaving.
- At `10:03:22Z`, the shared generator's Create Version PR job
  [124508002977](https://github.com/lgtm-hq/py-lintro/actions/runs/41191066047/job/124508002977)
  found no open version PR, then calculated `0.173.4` from `v0.173.3` reachable
  from the old triggering commit. The newer `v0.173.4` tag is not reachable from
  that commit, even though it already exists remotely (tagged at `10:03:10Z`).
- `check-version-files-changed.sh` accepted real version changes against that old
  checkout at `10:03:26Z`. At `10:03:28Z`, `peter-evans/create-pull-request`
  fetched newer main and applied those changes onto #3042. The version changes
  disappeared, leaving two changelog lines. Its earlier checks were not repeated
  against that new base. It recreated `release/v0.173.4` and opened #3051 at
  `10:03:33Z`. The already merged PR was absent from the open-PR lookup.
- [#3051](https://github.com/lgtm-hq/py-lintro/pull/3051) passed its PR checks but
  merged at `10:23:12Z` as `37b43f36d8a94a6b5adb508878943231e6a6dbb8`.
  Its `chore(release): version 0.173.4` subject requested the existing tag at a
  different commit. [Run 41192543597](https://github.com/lgtm-hq/py-lintro/actions/runs/41192543597)
  correctly failed: `Tag v0.173.4 already exists at 627cb4c0... but HEAD is 37b43f36...`.
- Original publication [41191159513](https://github.com/lgtm-hq/py-lintro/actions/runs/41191159513)
  was still in progress during this investigation; no GitHub Release existed yet.
  The old gate explicitly admitted unfinished publication, widening the window.

## Repository defenses

`check-last-publish-green.py` now reports `publish_green=false` whenever the newest
stable-tag publish is unfinished, including queued or approval-waiting runs. A forced
dispatch cannot bypass that condition. Completed-run policy is preserved, including
the startup-failure override. The early gate still warns and fails open on API errors;
it is not the final candidate validator.

The tokenless `finalize-version-pr.py` hook independently checks live origin before
running any finalizers. It rejects an existing candidate tag or a candidate that does
not advance live main's project version. It uses the SHA returned by `git ls-remote`,
not HEAD or a stale remote-tracking ref. Unreadable refs, an unavailable main object,
invalid versions or failed Git reads stop generation. These reads need no installation
token in this public repository and perform no fetch or ref mutation.

Focused regressions cover an unfinished publish (also with `--force`), workflow skip
semantics, a candidate consumed after an earlier gate passed (with or without its tag),
read failures and successful finalization for a fresh candidate. The auto-tag caller,
its shared tag collision check and the publication workflows are unchanged.

## Required upstream change (lgtm-ci; no queue edits here)

The root implementation is pinned at
`lgtm-hq/lgtm-ci@2097a900753e3e5ad0e336483501b278e99855ae` (`v0.78.1`), in
`.github/workflows/reusable-release-version-pr.yml`. This patch cannot place code
between its last caller hook and its credentialed PR write. Main can still advance
after the hook, so the repository defenses do not constitute an atomic fix.

The upstream recovery must:

1. Establish one fresh default-branch SHA for calculation, hook input and PR creation.
   Revalidate it immediately before writes. Abort and require fresh calculation when
   the base advances; do not let the PR action transplant an old candidate onto a newer
   base without validation.
2. Check the exact candidate tag at origin regardless of reachability from the trigger
   SHA. An existing tag means that candidate is consumed; do not recreate its branch or
   PR. Read failures must block the write.
3. Verify the generated ecosystem versions strictly advance the actual PR base and
   that the resulting diff changes those version stamps. Counting arbitrary
   non-changelog changes in `check-version-files-changed.sh` is insufficient.
4. Revalidate stale release PRs on base updates and merge-queue entry. A same-version
   changelog-only release PR must fail before merging, even if it was valid when opened.
5. Reproduce the exact interleaving: admit an old trigger, merge the first release and
   create its tag, then resume generation. Assert no duplicate branch/PR write occurs.
   Also cover a base advance after the hook, API/Git failure and a legitimate next bump.

Keep `reusable-release-auto-tag.yml` / `create-tag.sh` fail-closed for a tag pointing
at a different commit. Consume a tested upstream release under normal dependency-update
gates; no upstream repository, queue or pin was edited during this recovery.

## Minimal safe main recovery

1. Land this forward safety fix through normal review and required checks using an
   ordinary `fix(ci): ...` merge subject, not `chore(release): version ...`. The new
   main run will correctly skip release tagging for a non-release commit. Do not rerun
   #3051's failed auto-tag job expecting it to turn green: its historical collision is
   real and must remain a failure.
2. Keep `v0.173.4` at `627cb4c06dc22f97643aaf871559bf8b49b65464` and preserve any
   release/publication tied to it. The changelog-only #3051 needs no version rewrite
   or revert to restore the next main run. Do not move/delete tags or releases, approve
   deployments, republish, or dispatch release recovery as part of this fix.
3. Observe the original publication separately. Wait for its normal gates to finish;
   a partial or failed publication needs its own authorized recovery. Once this safety
   fix has landed, queues may resume under their normal gates. Admit a new release only
   after publication finishes and its candidate advances main and has no existing tag.
   If a new generation run is needed then, use a fresh run on current main. Never merge
   another `release/v0.173.4` PR.

Only a local signed bot commit is authorized for this task. No push, PR, issue comment,
deployment approval or publication action is part of the recovery execution.
