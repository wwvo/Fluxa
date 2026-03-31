"""Issue 发布后端。

本模块位于执行链路的最后一段，负责把 `RunSummary` 转为 issue 写操作。
它不关心 RSS 抓取细节，只负责模板渲染、issue 幂等查找、创建和更新。
当前支持两种发布后端：

- `github`：通过 `gh` 发布到 GitHub Issue
- `cnb`：通过 CNB API 发布到 CNB Issue
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from fluxa.models import PublishError, PublishState, RunSummary
from fluxa.render import render_run_issue
from fluxa.state import save_publish_state

_GH_TIMEOUT_SECONDS = 60
_CNB_TIMEOUT_SECONDS = 60
_SUPPORTED_PUBLISHERS = {"github", "cnb"}
_CNB_ACCEPT_HEADER = "application/vnd.cnb.api+json"
_CNB_USER_AGENT = "Fluxa/0.1 (+https://github.com/wwvo/Fluxa)"
_CNB_ISSUE_URL_PATTERN = re.compile(r"/-/issues/(?P<number>\d+)(?:\D*)$")


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
    display_key: str
    window_key: str


def publish_summaries(
    summary: RunSummary,
    templates_dir: Path,
    *,
    publishers: Sequence[str],
    repo: str | None,
    timezone_name: str,
    run_id: str | None,
    display_key: str | None,
    dry_run: bool,
    publish_state: PublishState | None = None,
    publish_state_path: Path | None = None,
    site_links: dict[str, str] | None = None,
) -> list[PublishResult]:
    publisher_names = _resolve_publishers(publishers)
    if repo is not None and len(publisher_names) > 1:
        raise PublishError(
            "多发布后端模式下不能同时传入 --repo，请改用 GITHUB_REPOSITORY / CNB_REPO 分别配置目标仓库"
        )

    draft = _build_issue_draft(
        templates_dir,
        summary,
        timezone_name=timezone_name,
        run_id=run_id,
        display_key=display_key,
        site_links=site_links,
    )

    if dry_run:
        # dry-run 仍然完整渲染 issue，方便本地核对模板和数据，但不触发实际写操作。
        return [
            _build_publish_result(
                publisher_name,
                _resolve_repo(
                    repo,
                    publisher=publisher_name,
                    required=False,
                ),
                draft,
                issue_number=None,
            )
            for publisher_name in publisher_names
        ]

    resolved_publish_state = _require_publish_state(
        publish_state,
        publish_state_path,
    )

    results: list[PublishResult] = []
    with tempfile.TemporaryDirectory(prefix="fluxa-") as temp_dir:
        temp_path = Path(temp_dir)
        issue_path = _write_issue_body(temp_path, draft.issue_body)

        for publisher_name in publisher_names:
            repo_name = _resolve_repo(
                repo,
                publisher=publisher_name,
                required=True,
            )
            if repo_name is None:
                raise PublishError("缺少目标仓库信息，请传入 --repo 或设置对应环境变量")
            issue_number = upsert_run_issue(
                publisher_name,
                repo_name,
                issue_title=draft.issue_title,
                issue_body_path=issue_path,
                existing_issue_number=resolved_publish_state.get_issue_number(
                    draft.window_key,
                    publisher_name,
                ),
            )
            resolved_publish_state.record_issue(
                window_key=draft.window_key,
                issue_date=draft.issue_date,
                display_key=draft.display_key,
                issue_title=draft.issue_title,
                run_id=draft.run_id,
                publisher=publisher_name,
                repo=repo_name,
                issue_number=issue_number,
            )
            save_publish_state(publish_state_path, resolved_publish_state)
            results.append(
                _build_publish_result(
                    publisher_name,
                    repo_name,
                    draft,
                    issue_number=issue_number,
                )
            )

    return results


def _build_issue_draft(
    templates_dir: Path,
    summary: RunSummary,
    *,
    timezone_name: str,
    run_id: str | None,
    display_key: str | None,
    site_links: dict[str, str] | None = None,
) -> _IssueDraft:
    resolved_run_id = _resolve_run_id(run_id)
    timezone = _load_timezone(timezone_name)
    run_time = datetime.now(timezone).replace(microsecond=0)
    issue_date = run_time.date().isoformat()
    resolved_display_key = _resolve_display_key(display_key, run_time)
    issue_title = f"Fluxa Digest | {issue_date} | run {resolved_run_id}"
    issue_body = render_run_issue(
        templates_dir,
        summary,
        issue_title=issue_title,
        display_key=resolved_display_key,
        timezone_name=timezone_name,
        timezone=timezone,
        run_id=resolved_run_id,
        run_time=run_time,
        site_links=site_links,
    )
    return _IssueDraft(
        issue_title=issue_title,
        issue_body=issue_body,
        run_id=resolved_run_id,
        issue_date=issue_date,
        display_key=resolved_display_key,
        window_key=f"{issue_date}|{resolved_display_key}",
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
    issue_body_path: Path,
    existing_issue_number: int | None,
) -> int:
    publisher_name = _resolve_publisher(publisher)
    if publisher_name == "github":
        return _upsert_github_run_issue(
            repo,
            issue_title=issue_title,
            issue_body_path=issue_body_path,
            existing_issue_number=existing_issue_number,
        )
    return _upsert_cnb_run_issue(
        repo,
        issue_title=issue_title,
        issue_body_path=issue_body_path,
        existing_issue_number=existing_issue_number,
    )


def _upsert_github_run_issue(
    repo: str,
    *,
    issue_title: str,
    issue_body_path: Path,
    existing_issue_number: int | None,
) -> int:
    if existing_issue_number is not None:
        _update_github_issue(repo, existing_issue_number, issue_title, issue_body_path)
        return existing_issue_number

    return _create_github_issue(repo, issue_title, issue_body_path)


def _upsert_cnb_run_issue(
    repo: str,
    *,
    issue_title: str,
    issue_body_path: Path,
    existing_issue_number: int | None,
) -> int:
    if existing_issue_number is not None:
        _update_cnb_issue(repo, existing_issue_number, issue_title, issue_body_path)
        return existing_issue_number

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


def _update_cnb_issue(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body_path: Path,
) -> None:
    if not _has_cnb_api_token():
        _update_cnb_issue_via_cli(repo, issue_number, issue_title, issue_body_path)
        return

    _request_cnb_empty(
        "PATCH",
        repo,
        issue_number=issue_number,
        payload={
            "title": issue_title,
            "body": issue_body_path.read_text(encoding="utf-8"),
        },
    )


def _create_cnb_issue(
    repo: str,
    issue_title: str,
    issue_body_path: Path,
) -> int:
    if not _has_cnb_api_token():
        return _create_cnb_issue_via_cli(repo, issue_title, issue_body_path)

    payload = _build_cnb_issue_payload(
        issue_title=issue_title,
        issue_body=issue_body_path.read_text(encoding="utf-8"),
    )
    created_issue = _request_cnb_json(
        "POST",
        repo,
        payload=payload,
    )
    issue_number = _coerce_issue_number(created_issue.get("number"))
    if issue_number is None:
        raise PublishError("创建 CNB issue 失败，未返回 issue number")
    return issue_number


def _build_cnb_issue_payload(
    *,
    issue_title: str,
    issue_body: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": issue_title,
        "body": issue_body,
    }
    labels = _read_env_csv("CNB_ISSUE_LABELS")
    if labels:
        payload["labels"] = labels

    assignees = _read_env_csv("CNB_ISSUE_ASSIGNEES")
    if assignees:
        payload["assignees"] = assignees

    return payload


def _build_cnb_issue_create_args() -> list[str]:
    """兼容无 Token 场景下的 cnb-rs CLI 发布。"""

    command_args: list[str] = []
    labels = _read_env_text("CNB_ISSUE_LABELS")
    if labels is not None:
        command_args.extend(["--labels", labels])

    assignees = _read_env_text("CNB_ISSUE_ASSIGNEES")
    if assignees is not None:
        command_args.extend(["--assignees", assignees])

    return command_args


def _update_cnb_issue_via_cli(
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
            str(issue_number),
            "--title",
            issue_title,
            "--body",
            issue_body,
        ]
    )


def _create_cnb_issue_via_cli(
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
        "--title",
        issue_title,
        "--body",
        issue_body,
    ]
    command.extend(_build_cnb_issue_create_args())
    output = _run_cnb(command)
    issue_number = _extract_cnb_issue_number_from_cli_output(output)
    if issue_number is None:
        raise PublishError("创建 CNB issue 失败，未返回 issue number")
    return issue_number


def _as_json_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(Mapping[str, object], value)


def _coerce_issue_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _require_publish_state(
    publish_state: PublishState | None,
    publish_state_path: Path | None,
) -> PublishState:
    if publish_state is None:
        raise PublishError("缺少发布账本状态，请先加载 publish-state.json 后再执行发布")
    if publish_state_path is None:
        raise PublishError("缺少发布账本路径，请传入 publish_state_path")
    return publish_state


def _read_env_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _read_env_csv(name: str) -> list[str]:
    raw_value = _read_env_text(name)
    if raw_value is None:
        return []
    values: list[str] = []
    for item in raw_value.split(","):
        normalized = item.strip()
        if normalized:
            values.append(normalized)
    return values


def _resolve_display_key(display_key: str | None, run_time: datetime) -> str:
    resolved = _read_env_text("FLUXA_DISPLAY_KEY")
    if display_key is not None:
        normalized = display_key.strip()
        if normalized:
            resolved = normalized
    if resolved is not None:
        return resolved
    return _build_time_window_display_key(run_time)


def _build_time_window_display_key(run_time: datetime) -> str:
    # 标题按固定 2 小时窗口展示，便于人工浏览，不影响 run_id 幂等标记。
    window_start_hour = (run_time.hour // 2) * 2
    window_start = run_time.replace(
        hour=window_start_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    window_end = window_start + timedelta(hours=2)
    return f"{window_start:%H:%M}-{window_end:%H:%M}"


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


def _resolve_publishers(publishers: Sequence[str]) -> tuple[str, ...]:
    normalized_publishers: list[str] = []
    for publisher in publishers:
        publisher_name = _resolve_publisher(publisher)
        if publisher_name not in normalized_publishers:
            normalized_publishers.append(publisher_name)
    if not normalized_publishers:
        raise PublishError("至少需要一个发布后端")
    return tuple(normalized_publishers)


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
    return _run_cli("gh", args, timeout=_GH_TIMEOUT_SECONDS, extra_env={"GH_PAGER": "cat"})


def _run_gh_json(args: list[str]) -> object:
    output = _run_gh(args)
    try:
        return cast(object, json.loads(output))
    except json.JSONDecodeError as exc:
        raise PublishError(f"gh JSON 输出解析失败: {' '.join(args)}") from exc


def _request_cnb_json(
    method: str,
    repo: str,
    *,
    payload: Mapping[str, object],
    issue_number: int | None = None,
) -> Mapping[str, object]:
    response = _request_cnb(
        method,
        repo,
        payload=payload,
        issue_number=issue_number,
    )
    try:
        parsed = cast(object, response.json())
    except json.JSONDecodeError as exc:
        raise PublishError(
            f"CNB API JSON 输出解析失败: {method} {response.request.url}"
        ) from exc

    payload_mapping = _as_json_mapping(parsed)
    if payload_mapping is None:
        raise PublishError("CNB API 返回了异常结果")
    return payload_mapping


def _request_cnb_empty(
    method: str,
    repo: str,
    *,
    payload: Mapping[str, object],
    issue_number: int,
) -> None:
    _ = _request_cnb(
        method,
        repo,
        payload=payload,
        issue_number=issue_number,
    )


def _request_cnb(
    method: str,
    repo: str,
    *,
    payload: Mapping[str, object],
    issue_number: int | None,
) -> httpx.Response:
    domain = _resolve_cnb_domain()
    token = _resolve_cnb_token(domain)
    url = _build_cnb_issue_api_url(domain, repo, issue_number=issue_number)
    headers = {
        "Accept": _CNB_ACCEPT_HEADER,
        "User-Agent": _CNB_USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    try:
        with httpx.Client(
            timeout=_CNB_TIMEOUT_SECONDS,
            headers=headers,
        ) as client:
            response = client.request(
                method,
                url,
                json=dict(payload),
            )
    except httpx.TimeoutException as exc:
        raise PublishError(
            f"CNB API 请求超时（>{_CNB_TIMEOUT_SECONDS} 秒）: {method} {url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise PublishError(f"CNB API 请求失败: {method} {url}\n{exc}") from exc

    if response.status_code >= 400:
        error_message = response.text.strip()
        raise PublishError(
            f"CNB API 请求失败 (HTTP {response.status_code}):\n{error_message}"
        )
    return response


def _resolve_cnb_domain() -> str:
    return _read_env_text("CNB_DOMAIN") or "cnb.cool"


def _resolve_cnb_token(domain: str) -> str:
    token = _lookup_cnb_token(domain)
    if token is None:
        raise PublishError(
            "缺少 CNB Token，请设置 CNB_TOKEN 或域名专用 CNB_TOKEN_* 环境变量"
        )
    return token


def _lookup_cnb_token(domain: str) -> str | None:
    domain_specific_env = f"CNB_TOKEN_{domain.replace('.', '').replace('-', '')}"
    return _read_env_text(domain_specific_env) or _read_env_text("CNB_TOKEN")


def _has_cnb_api_token() -> bool:
    return _lookup_cnb_token(_resolve_cnb_domain()) is not None


def _build_cnb_issue_api_url(
    domain: str,
    repo: str,
    *,
    issue_number: int | None,
) -> str:
    base_url = f"https://api.{domain}/{repo}/-/issues"
    if issue_number is None:
        return base_url
    return f"{base_url}/{issue_number}"


def _extract_cnb_issue_number_from_cli_output(output: str) -> int | None:
    normalized = output.strip()
    if not normalized:
        return None
    for line in reversed(normalized.splitlines()):
        match = _CNB_ISSUE_URL_PATTERN.search(line.strip())
        if match is None:
            continue
        return int(match.group("number"))
    return None


def _run_cnb(args: list[str]) -> str:
    return _run_cli("cnb-rs", args, timeout=_CNB_TIMEOUT_SECONDS)


def _run_cli(
    program: str,
    args: list[str],
    *,
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> str:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(
            [program, *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublishError(
            f"{program} 命令执行超时（>{timeout} 秒）: {' '.join(args)}"
        ) from exc
    if completed.returncode != 0:
        error_message = completed.stderr.strip() or completed.stdout.strip()
        raise PublishError(f"{program} 命令执行失败: {' '.join(args)}\n{error_message}")
    return completed.stdout
