#!/usr/bin/env python3
"""Score lintro review runs against archived competitor findings.

Produces overlap tables and draft gold-label candidates for human adjudication.
Competitor comments are baselines — not ground truth.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Any:
    """Load JSON from ``path``.

    Args:
        path: File path.

    Returns:
        Parsed JSON.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def _norm_path(path: str | None) -> str | None:
    """Normalize a file path for matching.

    Args:
        path: Raw path.

    Returns:
        Normalized path or ``None``.
    """
    if not path:
        return None
    return path.replace("\\", "/").lstrip("./")


def _finding_key(file: str | None, line: int | None) -> str | None:
    """Build a coarse location key.

    Args:
        file: File path.
        line: Line number.

    Returns:
        ``path:line`` key or ``None`` when location unknown.
    """
    path = _norm_path(file)
    if path is None or line is None:
        return None
    return f"{path}:{int(line)}"


def _token_set(text: str) -> set[str]:
    """Extract alphanumeric tokens for coarse text overlap.

    Args:
        text: Free text.

    Returns:
        Lowercased token set (length >= 4).
    """
    return {tok for tok in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{3,}", text.lower())}


def _load_competitor_findings(pr_dir: Path) -> list[dict[str, Any]]:
    """Load all competitor findings for one PR.

    Args:
        pr_dir: ``corpus/pr-<n>`` directory.

    Returns:
        Combined findings list.
    """
    competitors = pr_dir / "competitors"
    if not competitors.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(competitors.glob("*.json")):
        out.extend(_load_json(path))
    return out


def _load_lintro_findings(run_files: list[Path]) -> list[dict[str, Any]]:
    """Load findings from one or more lintro JSON run files.

    Args:
        run_files: Paths to review JSON outputs.

    Returns:
        Findings with ``run`` provenance attached.
    """
    out: list[dict[str, Any]] = []
    for path in run_files:
        try:
            payload = _load_json(path)
        except json.JSONDecodeError:
            continue
        for finding in payload.get("findings") or []:
            item = dict(finding)
            item["_run_file"] = path.name
            out.append(item)
    return out


def _overlap_stats(
    *,
    lintro: list[dict[str, Any]],
    competitors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute location and weak-text overlap stats.

    Args:
        lintro: Lintro findings.
        competitors: Competitor findings.

    Returns:
        Stats mapping.
    """
    comp_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in competitors:
        comp_by_source[finding.get("source") or "unknown"].append(finding)

    lintro_keys = {
        key
        for finding in lintro
        if (key := _finding_key(finding.get("file"), finding.get("line")))
    }

    by_source: dict[str, Any] = {}
    for source, findings in sorted(comp_by_source.items()):
        keys = {
            key
            for finding in findings
            if (key := _finding_key(finding.get("file"), finding.get("line")))
        }
        inter = lintro_keys & keys
        by_source[source] = {
            "competitor_located": len(keys),
            "lintro_located": len(lintro_keys),
            "location_overlap": len(inter),
            "jaccard": (
                round(len(inter) / len(lintro_keys | keys), 3)
                if (lintro_keys or keys)
                else None
            ),
            "competitor_total_comments": len(findings),
        }

    severity = Counter(str(f.get("severity") or "?") for f in lintro)
    return {
        "lintro_findings": len(lintro),
        "lintro_severity_hist": dict(severity),
        "lintro_located": len(lintro_keys),
        "per_competitor": by_source,
    }


def _draft_gold_candidates(
    *,
    competitors: list[dict[str, Any]],
    lintro: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Emit draft gold candidates from competitor+lintro union.

    Args:
        competitors: Competitor findings.
        lintro: Lintro findings.

    Returns:
        Candidate list for human adjudication.
    """
    candidates: list[dict[str, Any]] = []
    for finding in competitors:
        if finding.get("source") == "lintro_dogfood":
            continue
        candidates.append(
            {
                "origin": finding.get("source"),
                "file": finding.get("file"),
                "line": finding.get("line"),
                "title": finding.get("title"),
                "severity_guess": finding.get("severity_guess"),
                "url": finding.get("url"),
                "label": "unreviewed",  # must_catch | should_catch | noise | skip
                "notes": "",
            }
        )
    for finding in lintro:
        candidates.append(
            {
                "origin": "lintro_eval",
                "file": finding.get("file"),
                "line": finding.get("line"),
                "title": finding.get("title"),
                "severity_guess": finding.get("severity"),
                "url": None,
                "label": "unreviewed",
                "notes": "",
                "run_file": finding.get("_run_file"),
            }
        )
    return candidates


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Path to runs/<stamp>",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Markdown report output path",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    run_dir = args.run_dir
    if not run_dir.is_absolute():
        run_dir = root / run_dir
    corpus_dir = args.corpus_dir or (
        root / "evals" / "review-efficacy" / "corpus"
    )

    summary = _load_json(run_dir / "summary.json")
    per_pr: dict[str, Any] = {}

    for pr_dir in sorted(run_dir.glob("pr-*")):
        pr_num = int(pr_dir.name.split("-", 1)[1])
        corpus_pr = corpus_dir / pr_dir.name
        competitors = _load_competitor_findings(corpus_pr)
        run_files = sorted(pr_dir.glob("*.json"))
        lintro = _load_lintro_findings(run_files)
        stats = _overlap_stats(lintro=lintro, competitors=competitors)
        meta_path = corpus_pr / "meta.json"
        meta = _load_json(meta_path) if meta_path.exists() else {"number": pr_num}
        candidates = _draft_gold_candidates(competitors=competitors, lintro=lintro)
        cand_path = pr_dir / "gold.candidates.json"
        cand_path.write_text(
            json.dumps(candidates, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        per_pr[str(pr_num)] = {
            "title": meta.get("title"),
            "url": meta.get("url"),
            "competitor_counts": meta.get("competitor_counts"),
            "stats": stats,
            "gold_candidates": str(cand_path.relative_to(root)),
            "run_results": [
                r for r in summary.get("results") or [] if int(r.get("pr", -1)) == pr_num
            ],
        }

    score_path = run_dir / "scorecard.json"
    score_path.write_text(
        json.dumps({"run_dir": str(run_dir), "prs": per_pr}, indent=2) + "\n",
        encoding="utf-8",
    )

    report_path = args.report or (
        root / "evals" / "review-efficacy" / "reports" / f"{run_dir.name}.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Review efficacy scorecard — `{run_dir.name}`",
        "",
        f"- Depth: `{summary.get('depth')}`",
        f"- Runs per config: `{summary.get('runs')}`",
        f"- Repo: `{summary.get('repo')}`",
        "",
        "Competitor comments are baselines, not ground truth. Draft gold "
        "candidates were written per PR as `gold.candidates.json` for human "
        "labeling (`must_catch` / `should_catch` / `noise` / `skip`).",
        "",
        "| PR | Title | Lintro findings | Sev hist | Loc overlap (best bot) | Cost (avg) |",
        "| --- | --- | ---: | --- | --- | ---: |",
    ]
    for pr, data in sorted(per_pr.items(), key=lambda kv: int(kv[0])):
        stats = data["stats"]
        best = None
        for source, block in (stats.get("per_competitor") or {}).items():
            if source == "lintro_dogfood":
                continue
            tup = (block.get("location_overlap") or 0, source, block.get("jaccard"))
            if best is None or tup[0] > best[0]:
                best = tup
        best_s = f"{best[1]}:{best[0]} (J={best[2]})" if best else "—"
        costs = [
            r.get("cost_estimate_usd")
            for r in data["run_results"]
            if r.get("cost_estimate_usd") is not None
        ]
        avg_cost = round(sum(costs) / len(costs), 4) if costs else None
        title = (data.get("title") or "")[:48]
        lines.append(
            f"| #{pr} | {title} | {stats.get('lintro_findings')} | "
            f"`{stats.get('lintro_severity_hist')}` | {best_s} | {avg_cost} |"
        )
    lines.extend(
        [
            "",
            "## Per-PR competitor archives",
            "",
        ]
    )
    for pr, data in sorted(per_pr.items(), key=lambda kv: int(kv[0])):
        lines.append(f"### PR #{pr} — {data.get('title')}")
        lines.append("")
        lines.append(f"- URL: {data.get('url')}")
        lines.append(f"- Competitor counts: `{data.get('competitor_counts')}`")
        lines.append(f"- Stats: `{json.dumps(data['stats'], sort_keys=True)}`")
        lines.append(f"- Gold candidates: `{data.get('gold_candidates')}`")
        lines.append("")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {score_path}", flush=True)
    print(f"Wrote {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
