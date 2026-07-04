# Lintro vs. trunk, MegaLinter, pre-commit, and qlty

Choosing a linting and code-quality workflow means picking a point on a spectrum: from
thin hook runners to full quality platforms. This page positions Lintro honestly against
four common alternatives — [trunk.io][trunk], [MegaLinter][megalinter],
[pre-commit][precommit], and [qlty][qlty] — and is explicit about where each of them
wins.

We would rather lose an evaluation on the merits than win one on a claim that does not
survive a `--help`. If you find something here that is out of date, please
[open an issue](https://github.com/lgtm-hq/py-lintro/issues).

> **On accuracy:** competitor products change quickly. Feature and pricing claims below
> were checked against public documentation at the time of writing; where a capability
> could not be confidently verified it is marked accordingly. Always confirm against
> each project's current docs before making a decision.

## The short version

- **Lintro** is a free, MIT-licensed CLI that runs ~30 underlying tools behind one
  interface and normalizes their results into machine-readable output (JSON, SARIF, and
  more). It is local-first — the same command you run on your laptop runs in CI — and
  ships an optional bring-your-own-key AI code review.
- **trunk** is the most polished commercial option: the largest catalog, an IDE
  extension, and mature hold-the-line baselining. Some capabilities sit behind paid
  tiers.
- **MegaLinter** has the broadest CI-native language and format coverage, driven from a
  Docker image.
- **pre-commit** is the de facto standard for _managing git hooks_ — a different job
  than unifying linter output.
- **qlty** adds maintainability metrics, code smells, and duplication analysis on top of
  linting.

## Feature matrix

Legend: **Yes** = supported today · **No** = not supported · **Roadmap** = planned but
not shipped · **Partial** = supported with caveats (see footnotes) · **Unverified** =
could not be confirmed from public docs.

<!-- markdownlint-disable MD013 -->

| Capability                            | Lintro                                               | trunk                                               | MegaLinter                                                      | pre-commit                                     | qlty                                               |
| ------------------------------------- | ---------------------------------------------------- | --------------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------- | -------------------------------------------------- |
| Unified output formats (JSON / SARIF) | Yes — JSON, SARIF, grid, markdown, HTML, CSV, GitHub | Yes — JSON, SARIF                                   | Yes — SARIF + JSON reports                                      | No — passes each hook's raw output through     | Partial [^qlty-sarif]                              |
| Local-first CLI parity with CI        | Yes — same CLI locally and in CI                     | Yes                                                 | Partial — CI-first; local runs via Docker                       | Yes — git hook locally, `pre-commit run` in CI | Yes                                                |
| Fix + check semantics                 | Yes — `lintro fmt` / `lintro chk`                    | Yes — `trunk fmt` / `trunk check`                   | Yes — via `APPLY_FIXES`                                         | Depends on the hook                            | Yes — `qlty fmt` / `qlty check`                    |
| Hold-the-line / baseline              | Roadmap [^ln-baseline]                               | Yes — hold-the-line baseline                        | Partial — changed-files mode, no persistent baseline [^ml-diff] | No baseline — runs on staged files             | Partial [^qlty-newissues]                          |
| Diff-aware / changed-files-only runs  | Roadmap for lint [^ln-diff]                          | Yes                                                 | Yes — `VALIDATE_ALL_CODEBASE=false`                             | Yes — staged files by default                  | Yes — `--upstream` diff                            |
| Tool catalog size                     | ~30 (28 today)                                       | 100+                                                | 100+                                                            | Unbounded — any hook repo                      | 70+                                                |
| AI code review                        | Yes — BYO-key `lintro review --with-lint`            | Not a documented core capability [^ai]              | No                                                              | No                                             | No                                                 |
| License / pricing                     | MIT — free, open source                              | Proprietary CLI; free tier + paid Team / Enterprise | AGPL-3.0 — free, open source                                    | MIT — free, open source                        | Fair Source (BSL 1.1 → open); CLI free, paid Cloud |
| Install methods                       | pip / uv (PyPI), Docker (GHCR)                       | Shell installer, npm launcher                       | Docker image, CI action                                         | pip / uv                                       | Shell installer (macOS / Linux / Windows)          |

<!-- markdownlint-enable MD013 -->

[^ln-baseline]:
    Baseline / hold-the-line is tracked in
    [#438](https://github.com/lgtm-hq/py-lintro/issues/438) and is **not** shipped yet.
    Do not adopt Lintro today expecting this.

[^ln-diff]:
    A general diff / changed-files-only _lint_ mode is tracked in
    [#612](https://github.com/lgtm-hq/py-lintro/issues/612) and is **not** shipped yet.
    Note that the AI review command (`lintro review`) _is_ diff-based today via
    `--base`, `--uncommitted`, and `--pr`.

[^ml-diff]:
    MegaLinter can restrict validation to changed files, but this is a per-run filter,
    not a stored baseline of pre-existing issues.

[^qlty-sarif]:
    qlty produces machine-readable output; confirm SARIF availability and its exact form
    against current qlty documentation for your version.

[^qlty-newissues]:
    qlty surfaces new-versus-existing issues in its pull-request / Cloud workflow.
    Verify the current behavior for your setup.

[^ai]:
    AI-assisted code review was not a documented core capability of the other tools at
    the time of writing. Vendors add features quickly; verify against current docs.

## When to choose them instead

Every tool here is a reasonable choice for the right team. Lintro does not try to be all
of them.

### Choose trunk when you want the most polished, batteries-included platform

trunk has the largest catalog we surveyed (100+ linters and formatters), a first-class
IDE extension (the Trunk Code Quality VS Code plugin), and mature **hold-the-line**
baselining that flags only _new_ issues introduced by a change — which is the single
most effective way to adopt many linters on a large, pre-existing codebase without
drowning in legacy findings. If you want a commercially supported product, IDE
integration, and baselining today, trunk is the strongest option, and Lintro does not
yet match it on baselining (see [#438][438]).

### Choose pre-commit when hook management is the actual problem

pre-commit is the ecosystem standard for _managing and maintaining git hooks_. If your
goal is to declare a set of hooks in `.pre-commit-config.yaml`, pin them, and have every
clone run the same checks on commit, pre-commit is purpose-built for that and nothing
here replaces it. It is language-agnostic and has an enormous community of ready-made
hooks. It intentionally does **not** normalize output across those hooks — that is
simply not its job. Many teams run Lintro _inside_ a pre-commit hook and get both.

### Choose MegaLinter when you want the broadest CI coverage out of the box

MegaLinter bundles 100+ linters across dozens of languages and formats into a Docker
image designed to drop into CI with almost no configuration. If you have a polyglot
repository and want maximal language and format coverage from a single CI job —
including spell-checking, copy-paste detection, and security scanners — MegaLinter's
breadth is hard to beat. The trade-off is that it is CI-first and Docker-centric;
running it locally means running the container.

### Choose qlty when you care about maintainability metrics

qlty (from the team behind Code Climate) goes beyond linting to compute
**maintainability metrics** — code smells, cognitive and cyclomatic complexity,
duplication, and remediation estimates — across 40+ languages, with trends available in
qlty Cloud. If you want to track code health as a metric over time, not just pass or
fail a lint gate, qlty offers something Lintro does not. Note its CLI is Fair Source
(BSL 1.1 transitioning to open source), not a conventional OSI-approved license.

## When to choose Lintro

Lintro is the right fit when these matter to you:

- **Normalized, machine-readable output across ~30 tools.** Every underlying tool's
  findings are unified into one schema and can be emitted as JSON or SARIF (plus grid,
  markdown, HTML, CSV, and GitHub annotations). You parse one shape, not thirty. SARIF
  drops straight into GitHub code scanning and other SARIF consumers.
- **Local-first with true CLI/CI parity.** The exact command you run on your laptop —
  `lintro chk` / `lintro fmt` — is the one that runs in CI. No separate Docker-only
  path, no drift between what you see locally and what the pipeline enforces.
- **Bring-your-own-key AI code review.** `lintro review` performs diff-based AI review
  using _your_ Anthropic or OpenAI key — no vendor middleman and no per-seat
  subscription. Crucially, `--with-lint` runs Lintro's tools on the changed files and
  feeds those concrete findings into the review, so the AI is **grounded** in real
  linter output rather than guessing. Findings can be posted to a PR with `--post`.
- **Free and MIT-licensed.** No tiers, no seat counts, no commercial license to reason
  about.

### What Lintro does _not_ do yet

In the spirit of honesty:

- **No hold-the-line / baseline** — this is roadmap ([#438][438]). If you need to adopt
  many linters on a large legacy codebase _today_ without triaging every pre-existing
  finding, trunk or MegaLinter's changed-files mode will serve you better right now.
- **No general diff / changed-files-only lint mode** — roadmap ([#612][612]). (The AI
  review command is already diff-based.)
- **Smaller catalog** — ~30 tools versus 70–100+ for trunk, MegaLinter, and qlty. Lintro
  curates a focused, well-integrated set rather than maximizing count.

### A note on speed

Lintro runs its tools in parallel and is built for fast local iteration, but we are not
going to quote a speed number we have not measured. Reproducible benchmarks are in
progress ([#597](https://github.com/lgtm-hq/py-lintro/issues/597) /
[#598](https://github.com/lgtm-hq/py-lintro/issues/598)); this page will link them once
published. Until then, treat any speed comparison as unproven.

## Summary

If you want a **free, local-first, machine-readable** linting layer over a curated tool
set, with grounded BYO-key AI review, Lintro is built for you. If you need baselining,
the biggest catalog, IDE integration, pure hook management, or maintainability metrics
_today_, one of the alternatives above is the honest recommendation — and in several
cases you can run Lintro alongside it.

[trunk]: https://trunk.io/
[megalinter]: https://megalinter.io/
[precommit]: https://pre-commit.com/
[qlty]: https://qlty.sh/
[438]: https://github.com/lgtm-hq/py-lintro/issues/438
[612]: https://github.com/lgtm-hq/py-lintro/issues/612
