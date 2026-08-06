# Review efficacy scorecard — `pilot-depth1`

- Depth: `1`
- Runs per config: `2`
- Repo: `lgtm-hq/py-lintro`

Competitor comments are baselines, not ground truth. Draft gold candidates were written per PR as `gold.candidates.json` for human labeling (`must_catch` / `should_catch` / `noise` / `skip`).

| PR | Title | Lintro findings | Sev hist | Loc overlap (best bot) | Cost (avg) |
| --- | --- | ---: | --- | --- | ---: |
| #916 | chore(deps): update actions/cache to v6.1.0 (maj | 0 | `{}` | greptile:0 (J=0.0) | 0.4031 |
| #958 | fix(astro-check): run non-interactively to preve | 4 | `{'P2': 1, 'P3': 3}` | coderabbit:0 (J=0.0) | 0.9907 |
| #1186 | docs(ai): complete config reference and correct  | 3 | `{'P3': 2, 'P2': 1}` | coderabbit:0 (J=0.0) | 0.6294 |
| #1928 | fix(deps): bump fast-uri to 4.1.2 for CVE-2026-1 | 1 | `{'P3': 1}` | coderabbit:0 (J=0.0) | 0.2121 |
| #1936 | fix(deps): bump cryptography to 50.0.0 for GHSA- | 0 | `{}` | coderabbit:0 (J=None) | 0.2657 |
| #1958 | fix(review): render the sticky This-run stats as | 2 | `{'P3': 2}` | coderabbit:0 (J=0.0) | 0.7902 |

## Per-PR competitor archives

### PR #916 — chore(deps): update actions/cache to v6.1.0 (major) (major)

- URL: https://github.com/lgtm-hq/py-lintro/pull/916
- Competitor counts: `{'greptile': 10, 'macroscope': 1}`
- Stats: `{"lintro_findings": 0, "lintro_located": 0, "lintro_severity_hist": {}, "per_competitor": {"greptile": {"competitor_located": 4, "competitor_total_comments": 10, "jaccard": 0.0, "lintro_located": 0, "location_overlap": 0}, "macroscope": {"competitor_located": 1, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 0, "location_overlap": 0}}}`
- Gold candidates: `evals/review-efficacy/runs/pilot-depth1/pr-916/gold.candidates.json`

### PR #958 — fix(astro-check): run non-interactively to prevent prompt hang and timeout

- URL: https://github.com/lgtm-hq/py-lintro/pull/958
- Competitor counts: `{'coderabbit': 5, 'cursor_bugbot': 1, 'greptile': 2, 'lintro_dogfood': 1, 'macroscope': 1}`
- Stats: `{"lintro_findings": 4, "lintro_located": 3, "lintro_severity_hist": {"P2": 1, "P3": 3}, "per_competitor": {"coderabbit": {"competitor_located": 2, "competitor_total_comments": 5, "jaccard": 0.0, "lintro_located": 3, "location_overlap": 0}, "cursor_bugbot": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 3, "location_overlap": 0}, "greptile": {"competitor_located": 2, "competitor_total_comments": 2, "jaccard": 0.0, "lintro_located": 3, "location_overlap": 0}, "lintro_dogfood": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 3, "location_overlap": 0}, "macroscope": {"competitor_located": 1, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 3, "location_overlap": 0}}}`
- Gold candidates: `evals/review-efficacy/runs/pilot-depth1/pr-958/gold.candidates.json`

### PR #1186 — docs(ai): complete config reference and correct privacy claims

- URL: https://github.com/lgtm-hq/py-lintro/pull/1186
- Competitor counts: `{'coderabbit': 1, 'cursor_bugbot': 1, 'greptile': 16}`
- Stats: `{"lintro_findings": 3, "lintro_located": 3, "lintro_severity_hist": {"P2": 1, "P3": 2}, "per_competitor": {"coderabbit": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 3, "location_overlap": 0}, "cursor_bugbot": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 3, "location_overlap": 0}, "greptile": {"competitor_located": 13, "competitor_total_comments": 16, "jaccard": 0.0, "lintro_located": 3, "location_overlap": 0}}}`
- Gold candidates: `evals/review-efficacy/runs/pilot-depth1/pr-1186/gold.candidates.json`

### PR #1928 — fix(deps): bump fast-uri to 4.1.2 for CVE-2026-18446

- URL: https://github.com/lgtm-hq/py-lintro/pull/1928
- Competitor counts: `{'coderabbit': 1, 'lintro_dogfood': 1}`
- Stats: `{"lintro_findings": 1, "lintro_located": 1, "lintro_severity_hist": {"P3": 1}, "per_competitor": {"coderabbit": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 1, "location_overlap": 0}, "lintro_dogfood": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 1, "location_overlap": 0}}}`
- Gold candidates: `evals/review-efficacy/runs/pilot-depth1/pr-1928/gold.candidates.json`

### PR #1936 — fix(deps): bump cryptography to 50.0.0 for GHSA-g6cj-pr64-35w5

- URL: https://github.com/lgtm-hq/py-lintro/pull/1936
- Competitor counts: `{'coderabbit': 1, 'lintro_dogfood': 1}`
- Stats: `{"lintro_findings": 0, "lintro_located": 0, "lintro_severity_hist": {}, "per_competitor": {"coderabbit": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": null, "lintro_located": 0, "location_overlap": 0}, "lintro_dogfood": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": null, "lintro_located": 0, "location_overlap": 0}}}`
- Gold candidates: `evals/review-efficacy/runs/pilot-depth1/pr-1936/gold.candidates.json`

### PR #1958 — fix(review): render the sticky This-run stats as badge tables

- URL: https://github.com/lgtm-hq/py-lintro/pull/1958
- Competitor counts: `{'coderabbit': 4, 'lintro_dogfood': 1}`
- Stats: `{"lintro_findings": 2, "lintro_located": 1, "lintro_severity_hist": {"P3": 2}, "per_competitor": {"coderabbit": {"competitor_located": 1, "competitor_total_comments": 4, "jaccard": 0.0, "lintro_located": 1, "location_overlap": 0}, "lintro_dogfood": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 1, "location_overlap": 0}}}`
- Gold candidates: `evals/review-efficacy/runs/pilot-depth1/pr-1958/gold.candidates.json`

