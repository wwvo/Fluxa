"""轮询流程编排。

Runner 只负责把配置和状态送入抓取层，并产出一次运行的 `RunSummary`。
它是 CLI 层与抓取层之间的薄编排层，适合承接未来更复杂的调度策略。
"""

from __future__ import annotations

from fluxa.fetch import poll_feeds
from fluxa.models import AppConfig, AppState, RunSummary


def run_cycle(
    config: AppConfig,
    state: AppState,
    *,
    force_bootstrap: bool,
) -> RunSummary:
    # 先按当前配置收敛 state.feeds，移除已删除 feed 的旧状态，并补齐新增 feed 的空状态。
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
