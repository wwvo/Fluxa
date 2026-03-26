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
from fluxa.render import render_run_issue


@dataclass(slots=True, frozen=True)
class PublishResult:
    """一次发布动作的结果。"""

    repo: str
    issue_number: int | None
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
    issue_title = f"Fluxa Digest | {issue_date} | run {resolved_run_id}"

    issue_body = render_run_issue(
        templates_dir,
        summary,
        issue_title=issue_title,
        timezone_name=timezone_name,
        timezone=timezone,
        run_id=resolved_run_id,
        run_time=run_time,
    )

    if dry_run:
        # dry-run 仍然完整渲染 issue，方便本地核对模板和数据，但不触发 gh 写操作。
        return PublishResult(
            repo=repo_name,
            issue_number=None,
            issue_title=issue_title,
            run_id=resolved_run_id,
            issue_date=issue_date,
        )

    with tempfile.TemporaryDirectory(prefix="fluxa-") as temp_dir:
        temp_path = Path(temp_dir)
        issue_path = temp_path / "issue.md"
        issue_path.write_text(issue_body, encoding="utf-8")

        issue_number = upsert_run_issue(
            repo_name,
            issue_title=issue_title,
            run_marker=f"fluxa-run:{resolved_run_id}",
            issue_body_path=issue_path,
        )

    return PublishResult(
        repo=repo_name,
        issue_number=issue_number,
        issue_title=issue_title,
        run_id=resolved_run_id,
        issue_date=issue_date,
    )


def upsert_run_issue(
    repo: str,
    *,
    issue_title: str,
    run_marker: str,
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
            "100",
            "--json",
            "number,title,body",
        ]
    )

    if not isinstance(issues, list):
        raise PublishError("gh issue list 返回了异常结果")

    # 通过 HTML 注释里的 run_marker 做幂等匹配，workflow 重跑时会更新原 issue，而不是重复创建。
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        body = str(issue.get("body", ""))
        if f"<!-- {run_marker} -->" in body:
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
