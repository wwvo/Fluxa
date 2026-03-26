"""Fluxa CLI entry."""

from __future__ import annotations

import argparse
from pathlib import Path

from fluxa.config import load_config
from fluxa.models import FluxaError
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

    state.ensure_feeds([feed.id for feed in config.feeds])
    save_state(Path(args.state_path), state)

    enabled_count = len(config.enabled_feeds)
    total_count = len(config.feeds)
    print(
        f"Fluxa 已加载 {total_count} 个 feeds（启用 {enabled_count} 个），"
        f"状态文件已同步到 {args.state_path}"
    )
    if args.bootstrap_only:
        print("当前为 bootstrap-only 模式：本次仅初始化配置与状态骨架。")
    elif not state.bootstrap_completed:
        print("当前状态尚未完成 bootstrap；后续抓取阶段将先建立 seen_ids 再开始发布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
