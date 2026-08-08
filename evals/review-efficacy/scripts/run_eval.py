#!/usr/bin/env python3
"""Run ``lintro review`` over the efficacy corpus with fixed knobs.

Ephemerally enables AI review in ``.lintro-config.yaml`` (same helper as CI),
restores the file afterwards, and writes JSON outputs under ``runs/<stamp>/``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[3]


def _load_manifest(corpus_dir: Path) -> list[dict[str, Any]]:
    """Load corpus PR metadata list.

    Args:
        corpus_dir: Corpus root.

    Returns:
        List of PR meta dicts.
    """
    manifest_path = corpus_dir / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return list(data.get("prs") or [])


def _enable_ai(*, root: Path, max_cost_usd: float) -> Path:
    """Patch config via CI helper; return path to backup file.

    Args:
        root: Repo root.
        max_cost_usd: Cost cap for the session.

    Returns:
        Path to the config backup.
    """
    config = root / ".lintro-config.yaml"
    backup = root / ".lintro-config.yaml.eval-backup"
    shutil.copy2(config, backup)
    env = os.environ.copy()
    env["AI_REVIEW_MAX_COST_USD"] = str(max_cost_usd)
    result = subprocess.run(
        ["uv", "run", "python", "scripts/ci/enable_review_config.py"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        shutil.move(str(backup), str(config))
        raise RuntimeError(
            f"enable_review_config failed: {result.stderr or result.stdout}"
        )
    # Pin ai.review explicitly so we do not rely on the deprecated
    # ai.enabled-implies-both-features compatibility path.
    try:
        import yaml

        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        ai = data.setdefault("ai", {})
        if isinstance(ai, dict):
            ai["review"] = True
            ai["lint"] = False
            config.write_text(
                yaml.safe_dump(data, sort_keys=False),
                encoding="utf-8",
            )
    except Exception as exc:  # noqa: BLE001 - best-effort pin
        print(f"warning: could not pin ai.review explicitly: {exc}", flush=True)
    return backup


def _restore_ai(*, root: Path, backup: Path) -> None:
    """Restore ``.lintro-config.yaml`` from backup.

    Args:
        root: Repo root.
        backup: Backup path from :func:`_enable_ai`.
    """
    config = root / ".lintro-config.yaml"
    if backup.exists():
        shutil.move(str(backup), str(config))


def _run_one(
    *,
    root: Path,
    repo: str,
    pr: int,
    depth: int,
    timeout: float,
    with_lint: bool,
    out_path: Path,
) -> dict[str, Any]:
    """Invoke one ``lintro review`` and capture JSON.

    Args:
        root: Repo root.
        repo: ``owner/name``.
        pr: PR number.
        depth: Review depth.
        timeout: Provider timeout seconds.
        with_lint: Whether to pass ``--with-lint``.
        out_path: Destination JSON path.

    Returns:
        Run metadata including exit status and timings.
    """
    cmd = [
        "uv",
        "run",
        "lintro",
        "review",
        "--pr",
        str(pr),
        "--repo",
        repo,
        "--depth",
        str(depth),
        "--timeout",
        str(timeout),
        "--output",
        "json",
        "--advisory-tools",
        "none",
        "--transport",
        "cli",
    ]
    if with_lint:
        cmd.append("--with-lint")

    env = os.environ.copy()
    # OAuth token must not be mistaken for an API key.
    env.pop("ANTHROPIC_API_KEY", None)
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raise RuntimeError("CLAUDE_CODE_OAUTH_TOKEN is not set")

    started = time.time()
    result = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.time() - started
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.stdout or "", encoding="utf-8")
    if result.stderr:
        out_path.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")

    finding_count = None
    cost = None
    verdict = None
    try:
        payload = json.loads(result.stdout or "")
        findings = payload.get("findings") or []
        finding_count = len(findings)
        meta = payload.get("metadata") or {}
        cost = meta.get("cost_estimate_usd")
        verdict = payload.get("readiness_verdict")
    except json.JSONDecodeError:
        pass

    return {
        "pr": pr,
        "depth": depth,
        "with_lint": with_lint,
        "exit_code": result.returncode,
        "elapsed_sec": round(elapsed, 2),
        "finding_count": finding_count,
        "cost_estimate_usd": cost,
        "readiness_verdict": verdict,
        "output": str(out_path.relative_to(root)),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="lgtm-hq/py-lintro")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--prs",
        default=None,
        help="Optional comma-separated PR subset; default = full manifest",
    )
    parser.add_argument("--depth", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--runs", type=int, default=2, help="Repeats per PR/config")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument(
        "--with-lint",
        action="store_true",
        help="Also run a with-lint config (in addition to without)",
    )
    parser.add_argument("--max-cost-usd", type=float, default=8.0)
    parser.add_argument(
        "--stamp",
        default=None,
        help="Run directory stamp (default: UTC timestamp)",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    corpus_dir = args.corpus_dir or (
        root / "evals" / "review-efficacy" / "corpus"
    )
    prs_meta = _load_manifest(corpus_dir)
    if args.prs:
        wanted = {int(x) for x in args.prs.split(",") if x.strip()}
        prs_meta = [p for p in prs_meta if int(p["number"]) in wanted]

    stamp = args.stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_root = root / "evals" / "review-efficacy" / "runs" / stamp
    run_root.mkdir(parents=True, exist_ok=True)

    configs = [{"with_lint": False}]
    if args.with_lint:
        configs.append({"with_lint": True})

    backup = _enable_ai(root=root, max_cost_usd=args.max_cost_usd)
    summary: list[dict[str, Any]] = []
    try:
        for pr_meta in prs_meta:
            pr = int(pr_meta["number"])
            for config in configs:
                for run_idx in range(1, args.runs + 1):
                    lint_tag = "lint" if config["with_lint"] else "nolint"
                    out_path = (
                        run_root
                        / f"pr-{pr}"
                        / f"depth{args.depth}_{lint_tag}_run{run_idx}.json"
                    )
                    print(
                        f"Reviewing PR #{pr} depth={args.depth} "
                        f"{lint_tag} run={run_idx}/{args.runs}...",
                        flush=True,
                    )
                    try:
                        meta = _run_one(
                            root=root,
                            repo=args.repo,
                            pr=pr,
                            depth=args.depth,
                            timeout=args.timeout,
                            with_lint=bool(config["with_lint"]),
                            out_path=out_path,
                        )
                    except Exception as exc:  # noqa: BLE001 - harness boundary
                        meta = {
                            "pr": pr,
                            "depth": args.depth,
                            "with_lint": config["with_lint"],
                            "exit_code": -1,
                            "error": str(exc),
                        }
                    summary.append(meta)
                    print(f"  -> {meta}", flush=True)
    finally:
        _restore_ai(root=root, backup=backup)

    summary_path = run_root / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "stamp": stamp,
                "repo": args.repo,
                "depth": args.depth,
                "runs": args.runs,
                "results": summary,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
