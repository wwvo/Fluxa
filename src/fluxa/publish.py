"""使用 gh 发布到 GitHub issue。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fluxa.models import PublishError, RunSummary
from fluxa.render import render_daily_issue, render_run_comment


@dataclass(slots=True, frozen=True)
class PublishResult:
    """一次发布动作的结果。"""

    repo: str
    issue_number: int | None
    comment_id: int | None
    issue_title: str
    run_id: str
    issue_date: str


def publish_summary(
    summary: RunSummary,
    templates_dir: Path,
    *,
    repo: str | None,
    timezone_name: str,
    run_id: str | None,
    dry_run: bool,
) -> PublishResult:
    repo_name = _resolve_repo(repo)
    resolved_run_id = _resolve_run_id(run_id)
    timezone = _load_timezone(timezone_name)
    run_time = datetime.now(timezone).replace(microsecond=0)
    issue_date = run_time.date().isoformat()
    issue_title = f"Fluxa Digest | {issue_date}"

    issue_body = render_daily_issue(
        templates_dir,
        issue_title=issue_title,
        issue_date=issue_date,
        timezone_name=timezone_name,
        total_feeds=len(summary.config.feeds),
        enabled_feeds=len(summary.config.enabled_feeds),
    )
    comment_body = render_run_comment(
        templates_dir,
        summary,
        timezone_name=timezone_name,
        timezone=timezone,
        run_id=resolved_run_id,
        run_time=run_time,
    )

    if dry_run:
        return PublishResult(
            repo=repo_name,
            issue_number=None,
            comment_id=None,
            issue_title=issue_title,
            run_id=resolved_run_id,
            issue_date=issue_date,
        )

    with tempfile.TemporaryDirectory(prefix="fluxa-") as temp_dir:
        temp_path = Path(temp_dir)
        issue_path = temp_path / "issue.md"
        comment_path = temp_path / "comment.md"
        issue_path.write_text(issue_body, encoding="utf-8")
        comment_path.write_text(comment_body, encoding="utf-8")

        issue_number = ensure_daily_issue(
            repo_name,
            issue_title=issue_title,
            issue_marker=f"fluxa-issue:{issue_date}",
            issue_body_path=issue_path,
        )
        comment_id = upsert_run_comment(
            repo_name,
            issue_number=issue_number,
            run_marker=f"fluxa-run:{resolved_run_id}",
            comment_body_path=comment_path,
        )

    return PublishResult(
        repo=repo_name,
        issue_number=issue_number,
        comment_id=comment_id,
        issue_title=issue_title,
        run_id=resolved_run_id,
        issue_date=issue_date,
    )


def ensure_daily_issue(
    repo: str,
    *,
    issue_title: str,
    issue_marker: str,
    issue_body_path: Path,
) -> int:
    issues = _run_gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            "20",
            "--json",
            "number,title,body",
            "--search",
            f'"{issue_title}" in:title',
        ]
    )

    if not isinstance(issues, list):
        raise PublishError("gh issue list 返回了异常结果")

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        title = str(issue.get("title", ""))
        body = str(issue.get("body", ""))
        if title == issue_title or f"<!-- {issue_marker} -->" in body:
            issue_number = int(issue["number"])
            _run_gh(
                [
                    "api",
                    f"repos/{repo}/issues/{issue_number}",
                    "--method",
                    "PATCH",
                    "-f",
                    f"title={issue_title}",
                    "-F",
                    f"body=@{issue_body_path}",
                ]
            )
            return issue_number

    created = _run_gh_json(
        [
            "api",
            f"repos/{repo}/issues",
            "-f",
            f"title={issue_title}",
            "-F",
            f"body=@{issue_body_path}",
        ]
    )
    if not isinstance(created, dict) or "number" not in created:
        raise PublishError("创建 issue 失败，未返回 issue number")
    return int(created["number"])


def upsert_run_comment(
    repo: str,
    *,
    issue_number: int,
    run_marker: str,
    comment_body_path: Path,
) -> int:
    comments = _run_gh_json(
        [
            "api",
            f"repos/{repo}/issues/{issue_number}/comments?per_page=100",
        ]
    )

    if not isinstance(comments, list):
        raise PublishError("gh api comments 返回了异常结果")

    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body", ""))
        if f"<!-- {run_marker} -->" in body:
            comment_id = int(comment["id"])
            _run_gh(
                [
                    "api",
                    f"repos/{repo}/issues/comments/{comment_id}",
                    "--method",
                    "PATCH",
                    "-F",
                    f"body=@{comment_body_path}",
                ]
            )
            return comment_id

    created = _run_gh_json(
        [
            "api",
            f"repos/{repo}/issues/{issue_number}/comments",
            "-F",
            f"body=@{comment_body_path}",
        ]
    )
    if not isinstance(created, dict) or "id" not in created:
        raise PublishError("创建 issue comment 失败，未返回 comment id")
    return int(created["id"])


def _resolve_repo(repo: str | None) -> str:
    resolved = repo or os.getenv("GH_REPO") or os.getenv("GITHUB_REPOSITORY")
    if not resolved:
        raise PublishError(
            "缺少 GitHub 仓库信息，请传入 --repo 或设置 GITHUB_REPOSITORY"
        )
    return resolved


def _resolve_run_id(run_id: str | None) -> str:
    resolved = run_id or os.getenv("GITHUB_RUN_ID")
    if resolved:
        return str(resolved)
    return datetime.utcnow().strftime("manual-%Y%m%d%H%M%S")


def _load_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise PublishError(f"无效或不可用的时区: {timezone_name}") from exc


def _run_gh(args: list[str]) -> str:
    env = os.environ.copy()
    env["GH_PAGER"] = "cat"
    completed = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        error_message = completed.stderr.strip() or completed.stdout.strip()
        raise PublishError(f"gh 命令执行失败: {' '.join(args)}\n{error_message}")
    return completed.stdout


def _run_gh_json(args: list[str]) -> object:
    output = _run_gh(args)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise PublishError(f"gh JSON 输出解析失败: {' '.join(args)}") from exc
