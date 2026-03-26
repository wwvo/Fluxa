"""使用 gh 发布到 GitHub issue。

本模块位于执行链路的最后一段，负责把 `RunSummary` 转为 issue 写操作。
它不关心 RSS 抓取细节，只负责 issue 幂等查找、创建和更新。
"""

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

_GH_TIMEOUT_SECONDS = 60


@dataclass(slots=True, frozen=True)
class PublishResult:
    """一次发布动作的结果。"""

    repo: str | None
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
    repo_name = _resolve_repo(repo, required=not dry_run)
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
    issue_number = _find_run_issue_number(repo, run_marker)
    if issue_number is not None:
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


def _find_run_issue_number(repo: str, run_marker: str) -> int | None:
    marker = f"<!-- {run_marker} -->"
    page = 1

    while True:
        issues = _run_gh_json(
            [
                "api",
                f"repos/{repo}/issues",
                "-f",
                "state=all",
                "-f",
                "per_page=100",
                "-f",
                f"page={page}",
            ]
        )
        if not isinstance(issues, list):
            raise PublishError("gh issue 查询返回了异常结果")
        if not issues:
            return None

        for issue in issues:
            if not isinstance(issue, dict) or "pull_request" in issue:
                continue
            body = str(issue.get("body", ""))
            if marker not in body:
                continue
            issue_number = issue.get("number")
            if isinstance(issue_number, int) and not isinstance(issue_number, bool):
                return issue_number
            raise PublishError("命中的 issue 缺少有效的 number 字段")

        if len(issues) < 100:
            return None
        page += 1


def _resolve_repo(repo: str | None, *, required: bool) -> str | None:
    resolved = repo or os.getenv("GH_REPO") or os.getenv("GITHUB_REPOSITORY")
    if resolved:
        return resolved
    if required:
        raise PublishError(
            "缺少 GitHub 仓库信息，请传入 --repo 或设置 GITHUB_REPOSITORY"
        )
    return None


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
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublishError(
            f"gh 命令执行超时（>{_GH_TIMEOUT_SECONDS} 秒）: {' '.join(args)}"
        ) from exc
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
