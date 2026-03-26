"""运行结果输出与 GitHub Actions 汇总。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fluxa.models import FeedPollResult, RunSummary
from fluxa.publish import PublishResult

_WS_RE = re.compile(r"\s+")


def emit_run_report(
    summary: RunSummary,
    *,
    state_path: Path,
    dry_run: bool,
    publish_result: PublishResult | None,
) -> None:
    for line in _build_stdout_lines(
        summary,
        state_path=state_path,
        dry_run=dry_run,
        publish_result=publish_result,
    ):
        print(line)
    _write_github_step_summary(
        _build_step_summary_markdown(
            summary,
            state_path=state_path,
            dry_run=dry_run,
            publish_result=publish_result,
        )
    )


def _build_stdout_lines(
    summary: RunSummary,
    *,
    state_path: Path,
    dry_run: bool,
    publish_result: PublishResult | None,
) -> list[str]:
    total_count = len(summary.config.feeds)
    enabled_count = len(summary.config.enabled_feeds)
    lines = [
        f"Fluxa 已加载 {total_count} 个 feeds（启用 {enabled_count} 个），状态文件目标路径为 {state_path}",
        (
            f"本轮检查 {summary.checked_count} 个启用 feeds，新增 {summary.new_count} 篇，"
            f"错误 {summary.error_count} 个，304 / 无变化 {summary.not_modified_count} 个。"
        ),
        (
            f"备用源恢复 {len(summary.fallback_recovered_results)} 个，"
            f"从上轮错误恢复 {len(summary.recovered_results)} 个。"
        ),
    ]

    if summary.bootstrap_mode:
        lines.append("当前为 bootstrap 模式：本轮只建立 seen_ids，不发布历史文章。")
    elif summary.new_count == 0:
        lines.append("本轮没有新文章，不会发布 issue。")
    elif dry_run:
        lines.append("当前为 dry-run 模式：已跳过 gh 发布，也未保存 state。")
    elif publish_result is not None:
        lines.append(
            f"已发布到 {publish_result.repo} 的 issue #{publish_result.issue_number}。"
        )

    if not dry_run:
        lines.append("状态文件已保存。")

    if summary.failed_results:
        lines.append("失败 Feed 汇总：")
        lines.extend(_build_failed_result_lines(summary.failed_results))

    if summary.fallback_recovered_results:
        lines.append("备用源恢复汇总：")
        lines.extend(
            _build_recovered_result_lines(
                summary.fallback_recovered_results,
                include_window=False,
            )
        )

    recovered_with_primary = [
        result
        for result in summary.recovered_results
        if result not in summary.fallback_recovered_results
    ]
    if recovered_with_primary:
        lines.append("恢复抓取窗口提升：")
        lines.extend(
            _build_recovered_result_lines(
                recovered_with_primary,
                include_window=True,
            )
        )

    return lines


def _build_failed_result_lines(results: list[FeedPollResult]) -> list[str]:
    lines: list[str] = []
    for result in results:
        lines.append(
            f"- {result.feed.id} / {result.feed_title}: {_short_error(result.error or '未知错误')}"
        )
        lines.append(f"  尝试: {_format_attempts(result)}")
    return lines


def _build_recovered_result_lines(
    results: list[FeedPollResult],
    *,
    include_window: bool,
) -> list[str]:
    lines: list[str] = []
    for result in results:
        source_text = _short_source(result.source_url)
        line = f"- {result.feed.id} / {result.feed_title}: {source_text}"
        if include_window and result.effective_max_entries_per_feed is not None:
            line += f"；窗口 {result.effective_max_entries_per_feed}"
        lines.append(line)
        lines.append(f"  尝试: {_format_attempts(result)}")
    return lines


def _build_step_summary_markdown(
    summary: RunSummary,
    *,
    state_path: Path,
    dry_run: bool,
    publish_result: PublishResult | None,
) -> str:
    total_count = len(summary.config.feeds)
    enabled_count = len(summary.config.enabled_feeds)
    lines = [
        "# Fluxa Run Summary",
        "",
        f"- Feed 总数：{total_count}",
        f"- 启用 Feed：{enabled_count}",
        f"- 本轮检查：{summary.checked_count}",
        f"- 新增文章：{summary.new_count}",
        f"- 错误 Feed：{summary.error_count}",
        f"- 304 / 无变化：{summary.not_modified_count}",
        f"- 备用源恢复：{len(summary.fallback_recovered_results)}",
        f"- 从上轮错误恢复：{len(summary.recovered_results)}",
        f"- State 路径：`{state_path}`",
    ]

    if summary.bootstrap_mode:
        lines.append("- 当前处于 bootstrap 模式")
    elif dry_run:
        lines.append("- 当前处于 dry-run 模式")
    elif publish_result is not None and publish_result.issue_number is not None:
        lines.append(
            f"- 发布结果：[{publish_result.repo}#{publish_result.issue_number}](https://github.com/{publish_result.repo}/issues/{publish_result.issue_number})"
        )

    if summary.failed_results:
        lines.extend(["", "## 失败 Feed", ""])
        for result in summary.failed_results:
            lines.append(
                f"- `{result.feed.id}` / {result.feed_title}: {_short_error(result.error or '未知错误')}"
            )
            lines.append(f"  尝试：{_format_attempts(result)}")

    if summary.fallback_recovered_results:
        lines.extend(["", "## 备用源恢复", ""])
        for result in summary.fallback_recovered_results:
            lines.append(
                f"- `{result.feed.id}` / {result.feed_title}: {_short_source(result.source_url)}"
            )
            lines.append(f"  尝试：{_format_attempts(result)}")

    recovered_with_primary = [
        result
        for result in summary.recovered_results
        if result not in summary.fallback_recovered_results
    ]
    if recovered_with_primary:
        lines.extend(["", "## 恢复抓取窗口提升", ""])
        for result in recovered_with_primary:
            lines.append(
                f"- `{result.feed.id}` / {result.feed_title}: {_short_source(result.source_url)}；窗口 {result.effective_max_entries_per_feed}"
            )
            lines.append(f"  尝试：{_format_attempts(result)}")

    return "\n".join(lines).strip() + "\n"


def _write_github_step_summary(markdown: str) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        Path(summary_path).write_text(markdown, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 仅在 Actions 环境触发
        print(f"警告: 写入 GITHUB_STEP_SUMMARY 失败: {exc}")


def _format_attempts(result: FeedPollResult) -> str:
    if not result.attempts:
        return "无"

    parts: list[str] = []
    for attempt in result.attempts:
        detail = attempt.outcome
        if attempt.http_status is not None:
            detail += f"/{attempt.http_status}"
        if attempt.error:
            detail += f"/{_short_error(attempt.error)}"
        parts.append(
            f"{_short_source(attempt.source_url)}#{attempt.attempt_number} {detail}"
        )
    return " | ".join(parts)


def _short_source(source_url: str | None) -> str:
    if not source_url:
        return "未知来源"
    split_result = urlsplit(source_url)
    base = split_result.netloc or source_url
    path = split_result.path.rstrip("/")
    if not path:
        return base
    return f"{base}{path}"


def _short_error(error: str) -> str:
    cleaned = _WS_RE.sub(" ", error).strip()
    if len(cleaned) <= 140:
        return cleaned
    return f"{cleaned[:137]}..."
