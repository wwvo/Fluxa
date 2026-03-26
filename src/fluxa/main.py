"""Fluxa CLI entry."""

from __future__ import annotations

import argparse
import os
from copy import deepcopy
from pathlib import Path

from fluxa.config import load_config
from fluxa.models import FeedPollResult, FluxaError, RunSummary
from fluxa.publish import PublishResult, publish_summary
from fluxa.runner import run_cycle
from fluxa.state import load_state, save_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fluxa",
        description="Scheduled RSS digest publisher for Git repositories and GitHub Issues.",
    )
    parser.add_argument(
        "--config",
        default="feeds/feeds.yml",
        help="Path to the feed configuration file.",
    )
    parser.add_argument(
        "--state-path",
        default="state/state.json",
        help="Path to the state file in the checked out state branch workspace.",
    )
    parser.add_argument(
        "--bootstrap-only",
        action="store_true",
        help="Initialize feed state without publishing issue updates.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="GitHub repository in OWNER/REPO format. Defaults to GITHUB_REPOSITORY.",
    )
    parser.add_argument(
        "--templates-dir",
        default="templates",
        help="Directory containing the markdown templates.",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Shanghai",
        help="IANA timezone name used for issue date and rendered timestamps.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Unique run identifier used for issue idempotency. Defaults to GITHUB_RUN_ID.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render and execute the flow without saving state or publishing to GitHub.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = load_config(Path(args.config))
        state = load_state(Path(args.state_path))
    except FluxaError as exc:
        parser.exit(status=1, message=f"错误: {exc}\n")

    next_state = deepcopy(state)

    try:
        summary = run_cycle(
            config,
            next_state,
            force_bootstrap=args.bootstrap_only,
        )
    except FluxaError as exc:
        parser.exit(status=1, message=f"错误: {exc}\n")

    publish_result: PublishResult | None = None
    state_saved = False
    operation_error: FluxaError | None = None
    should_publish = summary.new_count > 0 and not summary.bootstrap_mode

    try:
        if should_publish:
            publish_result = publish_summary(
                summary,
                Path(args.templates_dir),
                repo=args.repo,
                timezone_name=args.timezone,
                run_id=args.run_id,
                dry_run=args.dry_run,
            )

        if not args.dry_run:
            save_state(Path(args.state_path), next_state)
            state_saved = True
    except FluxaError as exc:
        operation_error = exc

    _print_overview(
        config_path=args.config,
        state_path=args.state_path,
        summary=summary,
        publish_result=publish_result,
        dry_run=args.dry_run,
        state_saved=state_saved,
        total_count=len(config.feeds),
        enabled_count=len(config.enabled_feeds),
    )
    _print_result_sections(summary)
    _write_step_summary(
        summary,
        config_path=args.config,
        state_path=args.state_path,
        publish_result=publish_result,
        dry_run=args.dry_run,
        state_saved=state_saved,
        operation_error=operation_error,
        total_count=len(config.feeds),
        enabled_count=len(config.enabled_feeds),
    )

    if operation_error is not None:
        parser.exit(status=1, message=f"错误: {operation_error}\n")
    return 0


def _print_overview(
    *,
    config_path: str,
    state_path: str,
    summary: RunSummary,
    publish_result: PublishResult | None,
    dry_run: bool,
    state_saved: bool,
    total_count: int,
    enabled_count: int,
) -> None:
    print(
        f"Fluxa 已加载 {total_count} 个 feeds（启用 {enabled_count} 个），"
        f"配置文件为 {config_path}，状态文件目标路径为 {state_path}"
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
        print("当前为 dry-run 模式：已跳过 gh 发布，也未保存 state。")
    elif publish_result is not None:
        print(
            f"已发布到 {publish_result.repo} 的 issue #{publish_result.issue_number}。"
        )

    if not dry_run and state_saved:
        print("状态文件已保存。")


def _print_result_sections(summary: RunSummary) -> None:
    if summary.fallback_recovered_results:
        print("本轮由备用实例兜底的 feeds：")
        for result in summary.fallback_recovered_results:
            print(f"- {_format_recovery_line(result)}")

    direct_recoveries = [
        result
        for result in summary.recovered_results
        if not result.used_fallback
    ]
    if direct_recoveries:
        print("本轮恢复成功并扩大抓取窗口的 feeds：")
        for result in direct_recoveries:
            print(f"- {_format_recovery_line(result)}")

    if summary.failed_results:
        print("本轮失败的 feeds：")
        for result in summary.failed_results:
            print(f"- {_format_failure_line(result)}")


def _format_recovery_line(result: FeedPollResult) -> str:
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


def _format_failure_line(result: FeedPollResult) -> str:
    error_text = result.error or "未知错误"
    attempt_text = _format_attempts(result)
    return (
        f"`{result.feed.id}` / {result.feed_title}: {error_text}"
        f"；尝试 {attempt_text}"
    )


def _format_attempts(result: FeedPollResult) -> str:
    if not result.attempts:
        return "无"

    segments: list[str] = []
    for attempt in result.attempts:
        detail = attempt.status
        if attempt.http_status is not None:
            detail = f"{detail}:{attempt.http_status}"
        if attempt.error:
            detail = f"{detail}:{attempt.error}"
        segments.append(f"{attempt.source_url}#{attempt.attempt_number}({detail})")
    return " | ".join(segments)


def _write_step_summary(
    summary: RunSummary,
    *,
    config_path: str,
    state_path: str,
    publish_result: PublishResult | None,
    dry_run: bool,
    state_saved: bool,
    operation_error: FluxaError | None,
    total_count: int,
    enabled_count: int,
) -> None:
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not step_summary_path:
        return

    lines = [
        "# Fluxa Run Summary",
        "",
        f"- 配置文件：`{config_path}`",
        f"- 状态文件：`{state_path}`",
        f"- Feed 总数：{total_count}",
        f"- 启用 Feed：{enabled_count}",
        f"- 本轮检查：{summary.checked_count}",
        f"- 新增文章：{summary.new_count}",
        f"- 错误 Feed：{summary.error_count}",
        f"- 304 / 无变化：{summary.not_modified_count}",
        f"- bootstrap 模式：{'是' if summary.bootstrap_mode else '否'}",
        f"- dry-run：{'是' if dry_run else '否'}",
        f"- 状态已保存：{'是' if state_saved else '否'}",
    ]

    if publish_result is not None:
        lines.append(
            f"- 发布结果：issue #{publish_result.issue_number} @ `{publish_result.repo}`"
        )
    elif summary.new_count == 0 or summary.bootstrap_mode:
        lines.append("- 发布结果：本轮无需发布 issue")

    if operation_error is not None:
        lines.extend(["", f"- 操作错误：`{operation_error}`"])

    if summary.fallback_recovered_results:
        lines.extend(["", "## 备用实例兜底成功"])
        lines.extend(
            f"- {_format_recovery_line(result)}"
            for result in summary.fallback_recovered_results
        )

    direct_recoveries = [
        result
        for result in summary.recovered_results
        if not result.used_fallback
    ]
    if direct_recoveries:
        lines.extend(["", "## 失败后恢复成功"])
        lines.extend(
            f"- {_format_recovery_line(result)}" for result in direct_recoveries
        )

    if summary.failed_results:
        lines.extend(["", "## 失败 Feed"])
        for result in summary.failed_results:
            lines.append(f"- `{result.feed.id}` / {result.feed_title}")
            lines.append(f"  最终错误：{result.error or '未知错误'}")
            lines.append(f"  尝试：{_format_attempts(result)}")

    try:
        Path(step_summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"警告: 写入 GITHUB_STEP_SUMMARY 失败: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
