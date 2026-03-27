"""Issue 发布后端。

本模块位于执行链路的最后一段，负责把 `RunSummary` 转为 issue 写操作。
它不关心 RSS 抓取细节，只负责模板渲染、issue 幂等查找、创建和更新。
当前支持两种发布后端：

- `github`：通过 `gh` 发布到 GitHub Issue
- `cnb`：通过 `cnb-rs` 发布到 CNB Issue
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
_CNB_TIMEOUT_SECONDS = 60
_CNB_EMPTY_ISSUE_LIST_TEXT = "没有找到符合条件的 Issue"
_CNB_ISSUE_SEARCH_LIMIT = 20
_SUPPORTED_PUBLISHERS = {"github", "cnb"}


@dataclass(slots=True, frozen=True)
class PublishResult:
    """一次发布动作的结果。"""

    publisher: str
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
    publisher: str = "github",
    repo: str | None,
    timezone_name: str,
    run_id: str | None,
    dry_run: bool,
) -> PublishResult:
    publisher_name = _resolve_publisher(publisher)
    repo_name = _resolve_repo(
        repo,
        publisher=publisher_name,
        required=not dry_run,
    )
    draft = _build_issue_draft(
        templates_dir,
        summary,
        timezone_name=timezone_name,
        run_id=run_id,
    )

    if dry_run:
        # dry-run 仍然完整渲染 issue，方便本地核对模板和数据，但不触发 gh 写操作。
        return _build_publish_result(
            publisher_name,
            repo_name,
            draft,
            issue_number=None,
        )

    if repo_name is None:
        raise PublishError("缺少目标仓库信息，请传入 --repo 或设置对应环境变量")

    with tempfile.TemporaryDirectory(prefix="fluxa-") as temp_dir:
        temp_path = Path(temp_dir)
        issue_path = _write_issue_body(temp_path, draft.issue_body)

        issue_number = upsert_run_issue(
            publisher_name,
            repo_name,
            issue_title=draft.issue_title,
            run_marker=draft.run_marker,
            issue_body_path=issue_path,
            run_id=draft.run_id,
        )

    return _build_publish_result(
        publisher_name,
        repo_name,
        draft,
        issue_number=issue_number,
    )


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
    publisher: str,
    repo: str | None,
    draft: _IssueDraft,
    *,
    issue_number: int | None,
) -> PublishResult:
    return PublishResult(
        publisher=publisher,
        repo=repo,
        issue_number=issue_number,
        issue_title=draft.issue_title,
        run_id=draft.run_id,
        issue_date=draft.issue_date,
    )


def upsert_run_issue(
    publisher: str,
    repo: str,
    *,
    issue_title: str,
    run_marker: str,
    issue_body_path: Path,
    run_id: str,
) -> int:
    publisher_name = _resolve_publisher(publisher)
    if publisher_name == "github":
        return _upsert_github_run_issue(
            repo,
            issue_title=issue_title,
            run_marker=run_marker,
            issue_body_path=issue_body_path,
        )
    return _upsert_cnb_run_issue(
        repo,
        issue_title=issue_title,
        run_marker=run_marker,
        issue_body_path=issue_body_path,
        run_id=run_id,
    )


def _upsert_github_run_issue(
    repo: str,
    *,
    issue_title: str,
    run_marker: str,
    issue_body_path: Path,
) -> int:
    issue_number = _find_github_run_issue_number(repo, run_marker)
    if issue_number is not None:
        _update_github_issue(repo, issue_number, issue_title, issue_body_path)
        return issue_number

    return _create_github_issue(repo, issue_title, issue_body_path)


def _upsert_cnb_run_issue(
    repo: str,
    *,
    issue_title: str,
    run_marker: str,
    issue_body_path: Path,
    run_id: str,
) -> int:
    issue_number = _find_cnb_run_issue_number(repo, run_marker, run_id)
    if issue_number is not None:
        _update_cnb_issue(repo, issue_number, issue_title, issue_body_path)
        return issue_number

    return _create_cnb_issue(repo, issue_title, issue_body_path)


def _write_issue_body(temp_dir: Path, issue_body: str) -> Path:
    issue_path = temp_dir / "issue.md"
    _ = issue_path.write_text(issue_body, encoding="utf-8")
    return issue_path


def _update_github_issue(
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


def _create_github_issue(
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


def _find_github_run_issue_number(repo: str, run_marker: str) -> int | None:
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


def _find_cnb_run_issue_number(
    repo: str,
    run_marker: str,
    run_id: str,
) -> int | None:
    marker = _wrap_run_marker(run_marker)
    search_keyword = run_id.strip()
    if not search_keyword:
        return None

    for state in ("open", "closed"):
        candidate_numbers = _list_cnb_candidate_issue_numbers(
            repo,
            search_keyword=search_keyword,
            state=state,
        )
        for issue_number in candidate_numbers:
            issue = _view_cnb_issue(repo, issue_number)
            matched_issue_number = _match_issue_number(issue, marker)
            if matched_issue_number is not None:
                return matched_issue_number
    return None


def _list_cnb_candidate_issue_numbers(
    repo: str,
    *,
    search_keyword: str,
    state: str,
) -> list[int]:
    issues = _run_cnb_list_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--json",
            "--state",
            state,
            "--limit",
            str(_CNB_ISSUE_SEARCH_LIMIT),
            "--keyword",
            search_keyword,
        ]
    )
    issue_numbers: list[int] = []
    for issue in issues:
        issue_data = _as_json_mapping(issue)
        if issue_data is None:
            continue
        issue_number = _coerce_issue_number(issue_data.get("number"))
        if issue_number is not None:
            issue_numbers.append(issue_number)
    return issue_numbers


def _view_cnb_issue(repo: str, issue_number: int) -> Mapping[str, object]:
    issue = _run_cnb_json(
        [
            "issue",
            "view",
            "--repo",
            repo,
            "--json",
            str(issue_number),
        ]
    )
    issue_data = _as_json_mapping(issue)
    if issue_data is None:
        raise PublishError("cnb-rs issue view 返回了异常结果")
    return issue_data


def _update_cnb_issue(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body_path: Path,
) -> None:
    issue_body = issue_body_path.read_text(encoding="utf-8")
    _ = _run_cnb(
        [
            "issue",
            "edit",
            "--repo",
            repo,
            "--json",
            str(issue_number),
            "--title",
            issue_title,
            "--body",
            issue_body,
        ]
    )


def _create_cnb_issue(
    repo: str,
    issue_title: str,
    issue_body_path: Path,
) -> int:
    issue_body = issue_body_path.read_text(encoding="utf-8")
    command = [
        "issue",
        "create",
        "--repo",
        repo,
        "--json",
        "--title",
        issue_title,
        "--body",
        issue_body,
    ]
    command.extend(_build_cnb_issue_create_args())
    created = _run_cnb_json(command)
    created_issue = _as_json_mapping(created)
    if created_issue is None:
        raise PublishError("创建 CNB issue 失败，未返回 issue number")
    issue_number = _coerce_issue_number(created_issue.get("number"))
    if issue_number is None:
        raise PublishError("创建 CNB issue 失败，未返回 issue number")
    return issue_number


def _build_cnb_issue_create_args() -> list[str]:
    """将工作流里的标签与处理人透传给 cnb-rs。"""

    command_args: list[str] = []
    labels = _read_env_text("CNB_ISSUE_LABELS")
    if labels is not None:
        command_args.extend(["--labels", labels])

    assignees = _read_env_text("CNB_ISSUE_ASSIGNEES")
    if assignees is not None:
        command_args.extend(["--assignees", assignees])

    return command_args


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
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _read_env_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _resolve_repo(
    repo: str | None,
    *,
    publisher: str,
    required: bool,
) -> str | None:
    env_candidates = (
        ("GH_REPO", "GITHUB_REPOSITORY")
        if publisher == "github"
        else ("CNB_REPO", "GH_REPO", "GITHUB_REPOSITORY")
    )
    resolved = repo
    if not resolved:
        for env_name in env_candidates:
            resolved = os.getenv(env_name)
            if resolved:
                break
    if resolved:
        return resolved
    if required:
        raise PublishError("缺少目标仓库信息，请传入 --repo 或设置对应环境变量")
    return None


def _resolve_publisher(publisher: str) -> str:
    normalized = publisher.strip().lower()
    if normalized in _SUPPORTED_PUBLISHERS:
        return normalized
    supported = ", ".join(sorted(_SUPPORTED_PUBLISHERS))
    raise PublishError(f"不支持的发布后端: {publisher}（支持: {supported}）")


def _resolve_run_id(run_id: str | None) -> str:
    resolved = (
        run_id
        or os.getenv("FLUXA_RUN_ID")
        or os.getenv("GITHUB_RUN_ID")
        or os.getenv("CNB_PIPELINE_ID")
        or os.getenv("CNB_BUILD_ID")
        or os.getenv("CI_PIPELINE_ID")
        or os.getenv("CI_JOB_ID")
    )
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


def _run_cnb(args: list[str]) -> str:
    env = os.environ.copy()
    try:
        completed = subprocess.run(
            ["cnb-rs", *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=_CNB_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublishError(
            f"cnb-rs 命令执行超时（>{_CNB_TIMEOUT_SECONDS} 秒）: {' '.join(args)}"
        ) from exc
    if completed.returncode != 0:
        error_message = completed.stderr.strip() or completed.stdout.strip()
        raise PublishError(f"cnb-rs 命令执行失败: {' '.join(args)}\n{error_message}")
    return completed.stdout


def _run_cnb_json(args: list[str]) -> object:
    output = _run_cnb(args)
    try:
        return cast(object, json.loads(output))
    except json.JSONDecodeError as exc:
        raise PublishError(f"cnb-rs JSON 输出解析失败: {' '.join(args)}") from exc


def _run_cnb_list_json(args: list[str]) -> list[object]:
    output = _run_cnb(args).strip()
    if not output or output == _CNB_EMPTY_ISSUE_LIST_TEXT:
        return []
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise PublishError(f"cnb-rs JSON 输出解析失败: {' '.join(args)}") from exc
    if not isinstance(payload, list):
        raise PublishError("cnb-rs issue list 返回了异常结果")
    return cast(list[object], payload)
