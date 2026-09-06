# How to read a Lintro review report

The sticky PR comment is an index. Finding detail lives on inline comments.
Authoritative coverage state lives in workflow artifacts, not in the comment.

## Verdict

The title carries the derived verdict:

- **Blocked** — at least one open P1
- **Changes requested** — open P2, no P1
- **Nits only** — only open P3
- **Ready** — no open findings **and** every review-eligible file is covered at HEAD
- **Incomplete** — coverage-at-HEAD is below 100%. The findings-based label is withheld
  so a partial round can never look clean.

`~` on cost or tokens means the figure was estimated locally (subscription CLI, or a
provider that returned no usage counters). Subscription runs still show “what this would
have cost”; that figure is not a bill.

## Coverage and resume

A file is covered when its current normalized patch hash matches a stored entry.
Content-identical rebases keep coverage. The next round reviews never-reviewed files
first, then directly changed, then model-flagged, then group/import-invalidated files.

`--full` discards carried coverage for one run, and is also the only flag that forces a
round past the
[convergence stop rule](ai-features.md#review-convergence-deterministic-re-review-stop)
once it has fired — later pushes stay skipped until a `--full` run records a fresh
score. A skipped round's banner names any open P1 findings the last reviewed round left
behind; like a reviewed round's P1s, they do not redden the check.
`--max-cost-usd uncapped` lifts a flag/env cap. Overlay `0` is rejected; use `uncapped`
or a positive value.

## Update in place

The primary sticky updates in place. When history would overflow GitHub’s comment cap,
an archive sticky is created and the primary keeps heading, aggregates, and a link.

A pull request whose sticky comment predates schema v2 (lintro before #1916,
August 2026) is not migrated. Its comment is still updated in place, but the run history
behind it starts again from round 1: v1 recorded run totals with no round numbers and no
per-finding identity, so there was nothing to carry forward that would not have been
guessed.
