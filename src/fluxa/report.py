"""运行结果报告与格式化输出。

本模块负责把 RunSummary 和 PublishResult 转为终端输出和 GitHub Step Summary。
它是 main.py 和 CI 输出之间的桥梁，专注于展示层，不参与业务决策。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fluxa.models import FeedPollResult, FluxaError, RunSummary
from fluxa.publish import PublishResult


@dataclass(slots=True, frozen=True)
class RecoverySection:
    console_title: str
    markdown_title: str
    results: list[FeedPollResult]


def print_overview(
    *,
    config_path: str,
    state_path: str,
    publish_state_path: str,
    summary: RunSummary,
    publish_results: Sequence[PublishResult],
    dry_run: bool,
    feed_state_saved: bool,
    total_count: int,
    enabled_count: int,
) -> None:
    print(
        f"Fluxa 已加载 {total_count} 个 feeds（启用 {enabled_count} 个），"
        f"配置文件为 {config_path}，状态文件目标路径为 {state_path}，"
        f"发布账本路径为 {publish_state_path}"
    )
    print(
        f"本轮检查 {summary.checked_count} 个启用 feeds，"
        f"新增 {summary.new_count} 篇，错误 {summary.error_count} 个，"
        f"304 / 无变化 {summary.not_modified_count} 个。"
    )

    if summary.bootstrap_mode:
        print("当前为 bootstrap 模式：本轮只建立 seen_ids，不发布历史文章。")
    elif summary.new_count == 0:
        print("本轮没有新文章，不会发布 issue。")
    elif dry_run:
        print("当前为 dry-run 模式：已跳过以下 issue 发布，也未保存 state。")
        for publish_result in publish_results:
            publisher_label = publisher_display_name(publish_result.publisher)
            if publish_result.repo:
                print(f"- {publisher_label} issue @ {publish_result.repo}")
            else:
                print(f"- {publisher_label} issue")
    elif publish_results:
        print("已完成以下 issue 发布：")
        for publish_result in publish_results:
            publisher_label = publisher_display_name(publish_result.publisher)
            print(
                f"- {publisher_label} issue #{publish_result.issue_number} @ {publish_result.repo}"
            )

    if not dry_run and feed_state_saved:
        print("RSS 状态文件已保存。")


def print_result_sections(summary: RunSummary) -> None:
    for section in build_recovery_sections(summary):
        print(section.console_title)
        for result in section.results:
            print(f"- {format_recovery_line(result)}")

    if summary.failed_results:
        print("本轮失败的 feeds：")
        for result in summary.failed_results:
            print(f"- {format_failure_line(result)}")


def format_recovery_line(result: FeedPollResult) -> str:
    source_label = result.source_url or "unknown"
    boost_label = ""
    if (
        result.effective_max_entries_per_feed is not None
        and result.effective_max_entries_per_feed > result.feed.max_entries_per_feed
    ):
        boost_label = (
            f"，抓取窗口 {result.feed.max_entries_per_feed} -> "
            f"{result.effective_max_entries_per_feed}"
        )
    fallback_label = "，使用备用实例" if result.used_fallback else ""
    recovered_label = "，从上次失败恢复" if result.recovered_from_error else ""
    return (
        f"`{result.feed.id}` / {result.feed_title} -> {source_label}"
        f"（状态 {result.status}{fallback_label}{recovered_label}{boost_label}）"
    )


def format_failure_line(result: FeedPollResult) -> str:
    error_text = result.error or "未知错误"
    attempt_text = format_attempts(result)
    return (
        f"`{result.feed.id}` / {result.feed_title}: {error_text}；尝试 {attempt_text}"
    )


def format_attempts(result: FeedPollResult) -> str:
    if not result.attempts:
        return "无"

    segments: list[str] = []
    for attempt in result.attempts:
        detail = attempt.status
        if attempt.http_status is not None:
            detail = f"{detail}:{attempt.http_status}"
        if attempt.note:
            detail = f"{detail}:{attempt.note}"
        if attempt.error:
            detail = f"{detail}:{attempt.error}"
        segments.append(f"{attempt.source_url}#{attempt.attempt_number}({detail})")
    return " | ".join(segments)


def write_step_summary(
    summary: RunSummary,
    *,
    config_path: str,
    state_path: str,
    publish_state_path: str,
    publish_results: Sequence[PublishResult],
    dry_run: bool,
    feed_state_saved: bool,
    operation_error: FluxaError | None,
    total_count: int,
    enabled_count: int,
) -> None:
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not step_summary_path:
        return

    lines = _build_step_summary_header_lines(
        summary,
        config_path=config_path,
        state_path=state_path,
        publish_state_path=publish_state_path,
        dry_run=dry_run,
        feed_state_saved=feed_state_saved,
        total_count=total_count,
        enabled_count=enabled_count,
    )
    publish_lines = _build_publish_result_lines(
        summary,
        publish_results=publish_results,
        dry_run=dry_run,
    )
    lines.extend(publish_lines)
    if operation_error is not None:
        lines.extend(["", f"- 操作错误：`{operation_error}`"])

    for section in build_recovery_sections(summary):
        lines.extend(["", f"## {section.markdown_title}"])
        lines.extend(f"- {format_recovery_line(result)}" for result in section.results)

    if summary.failed_results:
        lines.extend(_build_failure_section_lines(summary.failed_results))

    try:
        Path(step_summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"警告: 写入 GITHUB_STEP_SUMMARY 失败: {exc}")


def _build_step_summary_header_lines(
    summary: RunSummary,
    *,
    config_path: str,
    state_path: str,
    publish_state_path: str,
    dry_run: bool,
    feed_state_saved: bool,
    total_count: int,
    enabled_count: int,
) -> list[str]:
    # Actions summary 主要服务于排障：先给整体数字，再追加恢复成功与失败明细。
    return [
        "# Fluxa Run Summary",
        "",
        f"- 配置文件：`{config_path}`",
        f"- RSS 状态文件：`{state_path}`",
        f"- 发布账本文件：`{publish_state_path}`",
        f"- Feed 总数：{total_count}",
        f"- 启用 Feed：{enabled_count}",
        f"- 本轮检查：{summary.checked_count}",
        f"- 新增文章：{summary.new_count}",
        f"- 错误 Feed：{summary.error_count}",
        f"- 304 / 无变化：{summary.not_modified_count}",
        f"- bootstrap 模式：{'是' if summary.bootstrap_mode else '否'}",
        f"- dry-run：{'是' if dry_run else '否'}",
        f"- RSS 状态已保存：{'是' if feed_state_saved else '否'}",
    ]


def _build_publish_result_lines(
    summary: RunSummary,
    *,
    publish_results: Sequence[PublishResult],
    dry_run: bool,
) -> list[str]:
    if publish_results:
        lines: list[str] = []
        if len(publish_results) > 1:
            lines.append("- 发布结果：")
        for publish_result in publish_results:
            publisher_label = publisher_display_name(publish_result.publisher)
            prefix = "- " if len(publish_results) == 1 else "  - "
            target = f"`{publish_result.repo}`" if publish_result.repo else "未解析仓库"
            if dry_run:
                lines.append(
                    f"{prefix}dry-run，已跳过向 {target} 写入 {publisher_label} issue"
                )
                continue
            lines.append(
                f"{prefix}{publisher_label} issue "
                f"#{publish_result.issue_number} @ {target}"
            )
        return lines

    if summary.new_count == 0 or summary.bootstrap_mode:
        return ["- 发布结果：本轮无需发布 issue"]
    return []


def _build_failure_section_lines(
    failed_results: list[FeedPollResult],
) -> list[str]:
    lines = ["", "## 失败 Feed"]
    for result in failed_results:
        lines.append(f"- `{result.feed.id}` / {result.feed_title}")
        lines.append(f"  最终错误：{result.error or '未知错误'}")
        lines.append(f"  尝试：{format_attempts(result)}")
    return lines


def build_recovery_sections(summary: RunSummary) -> list[RecoverySection]:
    sections: list[RecoverySection] = []

    if summary.fallback_recovered_results:
        sections.append(
            RecoverySection(
                console_title="本轮由备用实例兜底的 feeds：",
                markdown_title="备用实例兜底成功",
                results=summary.fallback_recovered_results,
            )
        )

    direct_recoveries = [
        result for result in summary.recovered_results if not result.used_fallback
    ]
    if direct_recoveries:
        sections.append(
            RecoverySection(
                console_title="本轮恢复成功并扩大抓取窗口的 feeds：",
                markdown_title="失败后恢复成功",
                results=direct_recoveries,
            )
        )

    return sections


def publisher_display_name(publisher: str) -> str:
    return "GitHub" if publisher == "github" else "CNB"
