"""Fluxa CLI 入口。

主流程按"加载配置 -> 加载状态 -> 执行轮询 -> 选择性发布 issue -> 保存状态"的顺序推进。
如果你想快速理解 Fluxa 一次完整运行是怎样串起来的，这个模块是最好的入口。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from fluxa.config import load_config
from fluxa.models import FluxaError
from fluxa.publish import PublishResult, publish_summaries
from fluxa.report import print_overview, print_result_sections, write_step_summary
from fluxa.runner import run_cycle
from fluxa.state import load_publish_state, load_state, save_state


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
        "--publish-state-path",
        default=None,
        help="Path to the publish ledger file. Defaults to a sibling publish-state.json next to --state-path.",
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
        help="Issue backend. Can be repeated. 'github' uses gh, 'cnb' uses CNB API.",
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
    publish_state_path = _resolve_publish_state_path(
        args.publish_state_path,
        args.state_path,
    )

    try:
        config = load_config(Path(args.config))
        state = load_state(Path(args.state_path))
        publish_state = load_publish_state(publish_state_path)
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
    feed_state_saved = False
    operation_error: FluxaError | None = None
    # bootstrap 以及"本轮无新增"都不应该发 issue，但依然要按成功结果刷新 state。
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
                publish_state=publish_state,
                publish_state_path=publish_state_path,
            )

        if not args.dry_run:
            save_state(Path(args.state_path), next_state)
            feed_state_saved = True
    except FluxaError as exc:
        operation_error = exc

    print_overview(
        config_path=args.config,
        state_path=args.state_path,
        publish_state_path=str(publish_state_path),
        summary=summary,
        publish_results=publish_results,
        dry_run=args.dry_run,
        feed_state_saved=feed_state_saved,
        total_count=len(config.feeds),
        enabled_count=len(config.enabled_feeds),
    )
    print_result_sections(summary)
    write_step_summary(
        summary,
        config_path=args.config,
        state_path=args.state_path,
        publish_state_path=str(publish_state_path),
        publish_results=publish_results,
        dry_run=args.dry_run,
        feed_state_saved=feed_state_saved,
        operation_error=operation_error,
        total_count=len(config.feeds),
        enabled_count=len(config.enabled_feeds),
    )

    if operation_error is not None:
        parser.exit(status=1, message=f"错误: {operation_error}\n")
    return 0


def _resolve_cli_publishers(raw_publishers: list[str] | None) -> list[str]:
    if not raw_publishers:
        return ["github"]
    publishers: list[str] = []
    for publisher in raw_publishers:
        if publisher not in publishers:
            publishers.append(publisher)
    return publishers


def _resolve_publish_state_path(
    raw_publish_state_path: str | None,
    state_path: str,
) -> Path:
    if raw_publish_state_path:
        return Path(raw_publish_state_path)
    resolved_state_path = Path(state_path)
    return resolved_state_path.with_name("publish-state.json")


if __name__ == "__main__":
    raise SystemExit(main())
