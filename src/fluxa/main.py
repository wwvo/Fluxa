"""Fluxa CLI entry."""

from __future__ import annotations

import argparse
from pathlib import Path

from fluxa.config import load_config
from fluxa.models import FluxaError
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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = load_config(Path(args.config))
        state = load_state(Path(args.state_path))
    except FluxaError as exc:
        parser.exit(status=1, message=f"错误: {exc}\n")

    summary = run_cycle(
        config,
        state,
        force_bootstrap=args.bootstrap_only,
    )
    save_state(Path(args.state_path), state)

    enabled_count = len(config.enabled_feeds)
    total_count = len(config.feeds)
    print(
        f"Fluxa 已加载 {total_count} 个 feeds（启用 {enabled_count} 个），"
        f"状态文件已同步到 {args.state_path}"
    )
    print(
        f"本轮检查 {summary.checked_count} 个启用 feeds，"
        f"新增 {summary.new_count} 篇，错误 {summary.error_count} 个。"
    )
    if summary.bootstrap_mode:
        print("当前为 bootstrap 模式：本轮只建立 seen_ids，不发布历史文章。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
