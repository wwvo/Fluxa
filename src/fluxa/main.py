"""Fluxa CLI 入口。

主流程按“加载配置 -> 加载状态 -> 执行轮询 -> 选择性发布 issue -> 保存状态”的顺序推进。
如果你想快速理解 Fluxa 一次完整运行是怎样串起来的，这个模块是最好的入口。
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from fluxa.config import load_config
from fluxa.models import FeedPollResult, FluxaError, RunSummary
from fluxa.publish import PublishResult, publish_summaries
from fluxa.runner import run_cycle
from fluxa.state import load_state, save_state


@dataclass(slots=True, frozen=True)
class _RecoverySection:
    console_title: str
    markdown_title: str
    results: list[FeedPollResult]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fluxa",
        description="Scheduled RSS digest publisher for Git repositories and GitHub/CNB Issues.",
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
        "--publisher",
        action="append",
        default=None,
        choices=("github", "cnb"),
        help="Issue backend. Can be repeated. 'github' uses gh, 'cnb' uses cnb-rs.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Target repository in OWNER/REPO format.",
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
        help="Unique run identifier used for issue idempotency.",
    )
    parser.add_argument(
        "--display-key",
        default=None,
        help="Human-readable issue title suffix. Defaults to the current 2-hour time window.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render and execute the flow without saving state or publishing issue.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    publishers = _resolve_cli_publishers(args.publisher)

    try:
        config = load_config(Path(args.config))
        state = load_state(Path(args.state_path))
    except FluxaError as exc:
        parser.exit(status=1, message=f"错误: {exc}\n")

    # 轮询过程会原地更新 state；先复制一份，确保发布失败时不会污染刚加载的旧状态。
    next_state = deepcopy(state)

    try:
        summary = run_cycle(
            config,
            next_state,
            force_bootstrap=args.bootstrap_only,
        )
    except FluxaError as exc:
        parser.exit(status=1, message=f"错误: {exc}\n")

    publish_results: list[PublishResult] = []
    state_saved = False
    operation_error: FluxaError | None = None
    # bootstrap 以及“本轮无新增”都不应该发 issue，但依然要按成功结果刷新 state。
    should_publish = summary.new_count > 0 and not summary.bootstrap_mode

    try:
        if should_publish:
            publish_results = publish_summaries(
                summary,
                Path(args.templates_dir),
                publishers=publishers,
                repo=args.repo,
                timezone_name=args.timezone,
                run_id=args.run_id,
                display_key=args.display_key,
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
        publish_results=publish_results,
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
        publish_results=publish_results,
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
    publish_results: Sequence[PublishResult],
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
        print("当前为 dry-run 模式：已跳过以下 issue 发布，也未保存 state。")
        for publish_result in publish_results:
            publisher_label = _publisher_display_name(publish_result.publisher)
            if publish_result.repo:
                print(f"- {publisher_label} issue @ {publish_result.repo}")
            else:
                print(f"- {publisher_label} issue")
    elif publish_results:
        print("已完成以下 issue 发布：")
        for publish_result in publish_results:
            publisher_label = _publisher_display_name(publish_result.publisher)
            print(
                f"- {publisher_label} issue #{publish_result.issue_number} @ {publish_result.repo}"
            )

    if not dry_run and state_saved:
        print("状态文件已保存。")


def _print_result_sections(summary: RunSummary) -> None:
    for section in _build_recovery_sections(summary):
        print(section.console_title)
        for result in section.results:
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
        f"`{result.feed.id}` / {result.feed_title}: {error_text}；尝试 {attempt_text}"
    )


def _format_attempts(result: FeedPollResult) -> str:
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


def _write_step_summary(
    summary: RunSummary,
    *,
    config_path: str,
    state_path: str,
    publish_results: Sequence[PublishResult],
    dry_run: bool,
    state_saved: bool,
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
        dry_run=dry_run,
        state_saved=state_saved,
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

    for section in _build_recovery_sections(summary):
        lines.extend(["", f"## {section.markdown_title}"])
        lines.extend(f"- {_format_recovery_line(result)}" for result in section.results)

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
    dry_run: bool,
    state_saved: bool,
    total_count: int,
    enabled_count: int,
) -> list[str]:
    # Actions summary 主要服务于排障：先给整体数字，再追加恢复成功与失败明细。
    return [
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
            publisher_label = _publisher_display_name(publish_result.publisher)
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
        lines.append(f"  尝试：{_format_attempts(result)}")
    return lines


def _build_recovery_sections(summary: RunSummary) -> list[_RecoverySection]:
    sections: list[_RecoverySection] = []

    if summary.fallback_recovered_results:
        sections.append(
            _RecoverySection(
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
            _RecoverySection(
                console_title="本轮恢复成功并扩大抓取窗口的 feeds：",
                markdown_title="失败后恢复成功",
                results=direct_recoveries,
            )
        )

    return sections


def _publisher_display_name(publisher: str) -> str:
    return "GitHub" if publisher == "github" else "CNB"


def _resolve_cli_publishers(raw_publishers: list[str] | None) -> list[str]:
    if not raw_publishers:
        return ["github"]
    publishers: list[str] = []
    for publisher in raw_publishers:
        if publisher not in publishers:
            publishers.append(publisher)
    return publishers


if __name__ == "__main__":
    raise SystemExit(main())
