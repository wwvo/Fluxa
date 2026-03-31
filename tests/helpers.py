"""测试共用工厂函数。"""

from __future__ import annotations

from pathlib import Path

from fluxa.models import AppConfig, FeedConfig, FeedDefaults, RunSummary


def build_feed(feed_id: str, url: str) -> FeedConfig:
    return FeedConfig(
        id=feed_id,
        url=url,
        fallback_urls=(),
        title=f"{feed_id} title",
        enabled=True,
        timeout_seconds=20,
        max_entries_per_feed=20,
        max_seen_ids=300,
    )


def build_summary() -> RunSummary:
    return RunSummary(
        config=AppConfig(
            path=Path("feeds/feeds.yml"),
            defaults=FeedDefaults(),
            feeds=(),
        ),
        bootstrap_mode=False,
        results=[],
    )
