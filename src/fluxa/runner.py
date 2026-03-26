"""轮询流程编排。"""

from __future__ import annotations

from fluxa.fetch import poll_feeds
from fluxa.models import AppConfig, AppState, RunSummary


def run_cycle(
    config: AppConfig,
    state: AppState,
    *,
    force_bootstrap: bool,
) -> RunSummary:
    state.ensure_feeds([feed.id for feed in config.feeds])
    bootstrap_mode = force_bootstrap or not state.bootstrap_completed
    results = poll_feeds(
        config.enabled_feeds,
        state.feeds,
        bootstrap_mode=bootstrap_mode,
    )
    if not state.bootstrap_completed:
        state.bootstrap_completed = True
    return RunSummary(
        config=config,
        bootstrap_mode=bootstrap_mode,
        results=results,
    )
