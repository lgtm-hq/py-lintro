#!/usr/bin/env python3
"""Build a review-efficacy corpus from GitHub PR bot comments.

Fetches PR metadata and normalizes issue/review comments from known AI review
bots into a common finding-shaped JSON schema under ``corpus/pr-<n>/``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_DEFAULT = "lgtm-hq/py-lintro"
BOT_LOGINS = {
    "coderabbitai[bot]": "coderabbit",
    "greptile-apps[bot]": "greptile",
    "macroscopeapp[bot]": "macroscope",
    "cursor[bot]": "cursor_bugbot",
}
LINTRO_MARKER = "<!-- lintro-ai-review -->"
FILE_LINE_RE = re.compile(
    r"(?P<file>[\w./\\-]+\.[A-Za-z0-9]+):(?P<line>\d+)",
)


def _repo_root() -> Path:
    """Return the repository root (parent of ``evals/``)."""
    return Path(__file__).resolve().parents[3]


def _gh_json(args: list[str]) -> Any:
    """Run ``gh`` and parse JSON stdout.

    Args:
        args: Arguments after ``gh``.

    Returns:
        Parsed JSON value.

    Raises:
        RuntimeError: When ``gh`` exits non-zero.
    """
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "gh failed"
        raise RuntimeError(msg)
    return json.loads(result.stdout) if result.stdout.strip() else None


def _normalize_body_finding(
    *,
    source: str,
    body: str,
    path: str | None,
    line: int | None,
    url: str,
    created_at: str,
    comment_id: int | str,
) -> dict[str, Any]:
    """Normalize one bot comment into a common finding dict.

    Args:
        source: Competitor key (e.g. ``coderabbit``).
        body: Raw markdown/HTML body.
        path: File path when known (inline comments).
        line: Line number when known.
        url: Comment URL.
        created_at: ISO timestamp.
        comment_id: Upstream comment id.

    Returns:
        Normalized finding mapping.
    """
    text = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
    title = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
    title = title[:160]
    if path is None or line is None:
        match = FILE_LINE_RE.search(text)
        if match:
            path = path or match.group("file")
            line = line or int(match.group("line"))
    severity = None
    lowered = text.lower()
    for label, needle in (
        ("P1", r"\bp0\b|\bp1\b|critical|blocker|high severity"),
        ("P2", r"\bp2\b|medium|major"),
        ("P3", r"\bp3\b|nit|minor|low severity|suggestion"),
    ):
        if re.search(needle, lowered):
            severity = label
            break
    return {
        "source": source,
        "comment_id": comment_id,
        "created_at": created_at,
        "url": url,
        "file": path,
        "line": line,
        "severity_guess": severity,
        "title": title,
        "body": text[:8000],
    }


def _collect_issue_comments(*, repo: str, pr: int) -> list[dict[str, Any]]:
    """Collect issue comments from competitor bots and lintro dogfood.

    Args:
        repo: ``owner/name``.
        pr: Pull request number.

    Returns:
        Normalized findings from issue-level comments.
    """
    comments = _gh_json(
        [
            "api",
            f"repos/{repo}/issues/{pr}/comments?per_page=100",
            "--paginate",
        ]
    )
    out: list[dict[str, Any]] = []
    for comment in comments or []:
        login = (comment.get("user") or {}).get("login", "")
        body = comment.get("body") or ""
        source = BOT_LOGINS.get(login)
        if source is None and login == "github-actions[bot]" and LINTRO_MARKER in body:
            source = "lintro_dogfood"
        if source is None:
            continue
        out.append(
            _normalize_body_finding(
                source=source,
                body=body,
                path=None,
                line=None,
                url=comment.get("html_url") or "",
                created_at=comment.get("created_at") or "",
                comment_id=comment.get("id"),
            )
        )
    return out


def _collect_review_comments(*, repo: str, pr: int) -> list[dict[str, Any]]:
    """Collect inline pull-request review comments from competitor bots.

    Args:
        repo: ``owner/name``.
        pr: Pull request number.

    Returns:
        Normalized findings from inline review comments.
    """
    comments = _gh_json(
        [
            "api",
            f"repos/{repo}/pulls/{pr}/comments?per_page=100",
            "--paginate",
        ]
    )
    out: list[dict[str, Any]] = []
    for comment in comments or []:
        login = (comment.get("user") or {}).get("login", "")
        source = BOT_LOGINS.get(login)
        if source is None:
            continue
        out.append(
            _normalize_body_finding(
                source=source,
                body=comment.get("body") or "",
                path=comment.get("path"),
                line=comment.get("line") or comment.get("original_line"),
                url=comment.get("html_url") or "",
                created_at=comment.get("created_at") or "",
                comment_id=comment.get("id"),
            )
        )
    return out


def _collect_pr_reviews(*, repo: str, pr: int) -> list[dict[str, Any]]:
    """Collect non-empty PR review bodies from competitor bots.

    Args:
        repo: ``owner/name``.
        pr: Pull request number.

    Returns:
        Normalized findings from top-level review summaries.
    """
    reviews = _gh_json(
        [
            "api",
            f"repos/{repo}/pulls/{pr}/reviews?per_page=100",
            "--paginate",
        ]
    )
    out: list[dict[str, Any]] = []
    for review in reviews or []:
        login = (review.get("user") or {}).get("login", "")
        source = BOT_LOGINS.get(login)
        body = (review.get("body") or "").strip()
        if source is None or not body:
            continue
        out.append(
            _normalize_body_finding(
                source=source,
                body=body,
                path=None,
                line=None,
                url=review.get("html_url") or "",
                created_at=review.get("submitted_at") or "",
                comment_id=review.get("id"),
            )
        )
    return out


def build_pr_fixture(*, repo: str, pr: int, corpus_dir: Path) -> Path:
    """Build one PR fixture directory.

    Args:
        repo: ``owner/name``.
        pr: Pull request number.
        corpus_dir: Corpus root directory.

    Returns:
        Path to the created ``pr-<n>`` directory.
    """
    meta = _gh_json(
        [
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            "number,title,body,baseRefName,headRefOid,baseRefOid,additions,"
            "deletions,changedFiles,mergedAt,url,files",
        ]
    )
    issue_findings = _collect_issue_comments(repo=repo, pr=pr)
    review_findings = _collect_review_comments(repo=repo, pr=pr)
    pr_review_findings = _collect_pr_reviews(repo=repo, pr=pr)
    all_findings = issue_findings + review_findings + pr_review_findings

    pr_dir = corpus_dir / f"pr-{pr}"
    competitors_dir = pr_dir / "competitors"
    competitors_dir.mkdir(parents=True, exist_ok=True)

    by_source: dict[str, list[dict[str, Any]]] = {}
    for finding in all_findings:
        by_source.setdefault(finding["source"], []).append(finding)

    for source, findings in by_source.items():
        (competitors_dir / f"{source}.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    summary = {
        "repo": repo,
        "number": pr,
        "title": meta.get("title"),
        "url": meta.get("url"),
        "base_ref": meta.get("baseRefName"),
        "base_oid": meta.get("baseRefOid"),
        "head_oid": meta.get("headRefOid"),
        "merged_at": meta.get("mergedAt"),
        "additions": meta.get("additions"),
        "deletions": meta.get("deletions"),
        "changed_files": meta.get("changedFiles"),
        "files": [f.get("path") for f in (meta.get("files") or [])],
        "competitor_counts": {k: len(v) for k, v in sorted(by_source.items())},
    }
    (pr_dir / "meta.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return pr_dir


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=REPO_DEFAULT,
        help=f"GitHub repo (default: {REPO_DEFAULT})",
    )
    parser.add_argument(
        "--prs",
        required=True,
        help="Comma-separated PR numbers",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help="Corpus output directory",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Sleep seconds between PRs (rate-limit cushion)",
    )
    args = parser.parse_args(argv)

    corpus_dir = args.corpus_dir or (
        _repo_root() / "evals" / "review-efficacy" / "corpus"
    )
    corpus_dir.mkdir(parents=True, exist_ok=True)

    pr_numbers = [int(part.strip()) for part in args.prs.split(",") if part.strip()]
    manifest: list[dict[str, Any]] = []
    for index, pr in enumerate(pr_numbers):
        print(f"Building corpus for PR #{pr}...", flush=True)
        pr_dir = build_pr_fixture(repo=args.repo, pr=pr, corpus_dir=corpus_dir)
        meta = json.loads((pr_dir / "meta.json").read_text(encoding="utf-8"))
        manifest.append(meta)
        print(f"  -> {pr_dir} counts={meta['competitor_counts']}", flush=True)
        if index + 1 < len(pr_numbers):
            time.sleep(args.sleep)

    manifest_path = corpus_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "repo": args.repo,
                "prs": manifest,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
