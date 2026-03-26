"""使用 gh 发布到 GitHub issue。

本模块位于执行链路的最后一段，负责把 `RunSummary` 转为 issue 写操作。
它不关心 RSS 抓取细节，只负责 issue 幂等查找、创建和更新。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
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


@dataclass(slots=True, frozen=True)
class _IssueDraft:
    issue_title: str
    issue_body: str
    run_id: str
    issue_date: str
    run_marker: str


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
    draft = _build_issue_draft(
        templates_dir,
        summary,
        timezone_name=timezone_name,
        run_id=run_id,
    )

    if dry_run:
        # dry-run 仍然完整渲染 issue，方便本地核对模板和数据，但不触发 gh 写操作。
        return _build_publish_result(repo_name, draft, issue_number=None)

    if repo_name is None:
        raise PublishError(
            "缺少 GitHub 仓库信息，请传入 --repo 或设置 GITHUB_REPOSITORY"
        )

    with tempfile.TemporaryDirectory(prefix="fluxa-") as temp_dir:
        temp_path = Path(temp_dir)
        issue_path = _write_issue_body(temp_path, draft.issue_body)

        issue_number = upsert_run_issue(
            repo_name,
            issue_title=draft.issue_title,
            run_marker=draft.run_marker,
            issue_body_path=issue_path,
        )

    return _build_publish_result(repo_name, draft, issue_number=issue_number)


def _build_issue_draft(
    templates_dir: Path,
    summary: RunSummary,
    *,
    timezone_name: str,
    run_id: str | None,
) -> _IssueDraft:
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
    return _IssueDraft(
        issue_title=issue_title,
        issue_body=issue_body,
        run_id=resolved_run_id,
        issue_date=issue_date,
        run_marker=f"fluxa-run:{resolved_run_id}",
    )


def _build_publish_result(
    repo: str | None,
    draft: _IssueDraft,
    *,
    issue_number: int | None,
) -> PublishResult:
    return PublishResult(
        repo=repo,
        issue_number=issue_number,
        issue_title=draft.issue_title,
        run_id=draft.run_id,
        issue_date=draft.issue_date,
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
        _update_issue(repo, issue_number, issue_title, issue_body_path)
        return issue_number

    return _create_issue(repo, issue_title, issue_body_path)


def _write_issue_body(temp_dir: Path, issue_body: str) -> Path:
    issue_path = temp_dir / "issue.md"
    _ = issue_path.write_text(issue_body, encoding="utf-8")
    return issue_path


def _update_issue(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body_path: Path,
) -> None:
    _ = _run_gh(
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


def _create_issue(
    repo: str,
    issue_title: str,
    issue_body_path: Path,
) -> int:
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
    created_issue = _as_json_mapping(created)
    if created_issue is None:
        raise PublishError("创建 issue 失败，未返回 issue number")
    issue_number = _coerce_issue_number(created_issue.get("number"))
    if issue_number is None:
        raise PublishError("创建 issue 失败，未返回 issue number")
    return issue_number


def _find_run_issue_number(repo: str, run_marker: str) -> int | None:
    marker = _wrap_run_marker(run_marker)
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
        issue_items = _as_json_list(issues)
        if issue_items is None:
            raise PublishError("gh issue 查询返回了异常结果")
        if not issue_items:
            return None

        for issue in issue_items:
            issue_number = _match_issue_number(issue, marker)
            if issue_number is not None:
                return issue_number

        if len(issue_items) < 100:
            return None
        page += 1


def _match_issue_number(issue: object, marker: str) -> int | None:
    issue_data = _as_json_mapping(issue)
    if issue_data is None or "pull_request" in issue_data:
        return None
    body = _coerce_text(issue_data.get("body"))
    if marker not in body:
        return None
    issue_number = _coerce_issue_number(issue_data.get("number"))
    if issue_number is not None:
        return issue_number
    raise PublishError("命中的 issue 缺少有效的 number 字段")


def _wrap_run_marker(run_marker: str) -> str:
    return f"<!-- {run_marker} -->"


def _as_json_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return cast(list[object], value)


def _as_json_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(Mapping[str, object], value)


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_issue_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


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
    return datetime.now(tz=UTC).strftime("manual-%Y%m%d%H%M%S")


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
        return cast(object, json.loads(output))
    except json.JSONDecodeError as exc:
        raise PublishError(f"gh JSON 输出解析失败: {' '.join(args)}") from exc
