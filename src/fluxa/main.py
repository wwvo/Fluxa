"""Fluxa CLI entry."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from fluxa.config import load_config
from fluxa.models import FluxaError
from fluxa.publish import publish_summary
from fluxa.report import emit_run_report
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
        publish_result = None
        should_publish = summary.new_count > 0 and not summary.bootstrap_mode
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
    except FluxaError as exc:
        parser.exit(status=1, message=f"错误: {exc}\n")

    emit_run_report(
        summary,
        state_path=Path(args.state_path),
        dry_run=args.dry_run,
        publish_result=publish_result,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
