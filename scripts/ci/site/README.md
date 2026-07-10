# Site CI scripts

| Script                          | Purpose                                                                    |
| ------------------------------- | -------------------------------------------------------------------------- |
| `migrate-docs-content.py`       | Copy repo-root `docs/` into `apps/site/src/content/docs/` with frontmatter |
| `build.sh`                      | Build Astro site (`ASTRO_BASE` defaults from `defaults.env`)               |
| `check.sh`                      | `astro check` and dependency install                                       |
| `test.sh`                       | Vitest with coverage                                                       |
| `test-python.sh`                | Pytest for `tests/scripts/ci/`                                             |
| `test-all.sh`                   | `test.sh` + `test-python.sh`                                               |
| `prepare-lychee-action-args.sh` | Strip duplicate lychee flags for `lychee-action`                           |
| `preview-serve.sh`              | `astro preview` with `ASTRO_BASE` from `defaults.env`                      |
| `preview-pages-local.sh`        | Build dist + optional local coverage bundles for manual Pages preview      |

## Astro base path

[`defaults.env`](defaults.env) defines `ASTRO_BASE_DEFAULT` (currently `/py-lintro/`).
[`build.sh`](build.sh) and the root [`Makefile`](../../../Makefile) `SITE_ASTRO_BASE`
target read that value — do not duplicate the path elsewhere.

| Context                              | `ASTRO_BASE`                             |
| ------------------------------------ | ---------------------------------------- |
| Local `make site-dev` / `site-build` | `ASTRO_BASE_DEFAULT` from `defaults.env` |
| `site-quality.yml` link check build  | `/` (root-relative hrefs under `dist/`)  |
| `deploy-pages.yml` production deploy | `ASTRO_BASE_DEFAULT` via `build.sh`      |

## GitHub Pages (Model B: site + bundled reports)

Deploy uses **lgtm-ci**
[`reusable-deploy-site-with-reports`](https://github.com/lgtm-hq/lgtm-ci/blob/main/.github/workflows/reusable-deploy-site-with-reports.yml)
via [`.github/workflows/deploy-pages.yml`](../../../.github/workflows/deploy-pages.yml).

1. **CI - Tests** uploads `coverage-html` on `main` (`stage-coverage-html` job).
2. **Deploy - GitHub Pages** runs on `workflow_run` after **CI - Tests** or **Quality -
   Documentation Site** succeeds on `main`, or via `workflow_dispatch`.
3. The reusable workflow builds `apps/site/dist`, merges artifacts per
   [`.github/pages-bundle-manifest.json`](../../../.github/pages-bundle-manifest.json),
   and publishes to GitHub Pages.

| Published path                                  | Content                            |
| ----------------------------------------------- | ---------------------------------- |
| `https://lgtm-hq.github.io/py-lintro/`          | Astro documentation site           |
| `https://lgtm-hq.github.io/py-lintro/coverage/` | Python pytest HTML coverage report |

**Settings → Pages → Build and deployment → Source: GitHub Actions** (not “Deploy from a
branch”).
