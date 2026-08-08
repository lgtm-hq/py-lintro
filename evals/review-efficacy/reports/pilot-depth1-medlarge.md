# Review efficacy scorecard — `pilot-depth1-medlarge`

- Depth: `1`
- Runs per config: `1`
- Repo: `lgtm-hq/py-lintro`

Competitor comments are baselines, not ground truth. Draft gold candidates were written per PR as `gold.candidates.json` for human labeling (`must_catch` / `should_catch` / `noise` / `skip`).

| PR | Title | Lintro findings | Sev hist | Loc overlap (best bot) | Cost (avg) |
| --- | --- | ---: | --- | --- | ---: |
| #1886 | feat(cli): move advisory AI finders out of chk i | 0 | `{}` | coderabbit:0 (J=None) | None |
| #1939 | feat(review): add corpus-informed finding model  | 1 | `{'P3': 1}` | coderabbit:0 (J=0.0) | 1.8539 |

## Per-PR competitor archives

### PR #1886 — feat(cli): move advisory AI finders out of chk into lintro review

- URL: https://github.com/lgtm-hq/py-lintro/pull/1886
- Competitor counts: `{'coderabbit': 1, 'lintro_dogfood': 1}`
- Stats: `{"lintro_findings": 0, "lintro_located": 0, "lintro_severity_hist": {}, "per_competitor": {"coderabbit": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": null, "lintro_located": 0, "location_overlap": 0}, "lintro_dogfood": {"competitor_located": 0, "competitor_total_comments": 1, "jaccard": null, "lintro_located": 0, "location_overlap": 0}}}`
- Gold candidates: `evals/review-efficacy/runs/pilot-depth1-medlarge/pr-1886/gold.candidates.json`

### PR #1939 — feat(review): add corpus-informed finding model to the review pipeline

- URL: https://github.com/lgtm-hq/py-lintro/pull/1939
- Competitor counts: `{'coderabbit': 7, 'lintro_dogfood': 1}`
- Stats: `{"lintro_findings": 1, "lintro_located": 1, "lintro_severity_hist": {"P3": 1}, "per_competitor": {"coderabbit": {"competitor_located": 5, "competitor_total_comments": 7, "jaccard": 0.0, "lintro_located": 1, "location_overlap": 0}, "lintro_dogfood": {"competitor_located": 1, "competitor_total_comments": 1, "jaccard": 0.0, "lintro_located": 1, "location_overlap": 0}}}`
- Gold candidates: `evals/review-efficacy/runs/pilot-depth1-medlarge/pr-1939/gold.candidates.json`

