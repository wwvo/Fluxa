"""基于 seen_ids 的增量计算。

本模块只关心“当前抓到的条目”和“历史已见过的条目”之间的差集。
它不处理抓取细节，也不处理渲染发布，是 Fluxa 增量语义的核心边界。
"""

from __future__ import annotations

from collections.abc import Sequence

from fluxa.models import NormalizedEntry


def compute_entry_delta(
    entries: Sequence[NormalizedEntry],
    seen_ids: Sequence[str],
    *,
    max_seen_ids: int,
    suppress_new_entries: bool,
) -> tuple[list[NormalizedEntry], list[str]]:
    unique_entries: list[NormalizedEntry] = []
    current_ids: list[str] = []
    current_id_set: set[str] = set()

    # 同一轮抓取里可能出现重复条目，先按 entry_id 去重，再和历史 seen_ids 做增量比较。
    for entry in entries:
        if entry.entry_id in current_id_set:
            continue
        current_id_set.add(entry.entry_id)
        current_ids.append(entry.entry_id)
        unique_entries.append(entry)

    existing_ids = set(seen_ids)
    new_entries = [
        entry for entry in unique_entries if entry.entry_id not in existing_ids
    ]
    if suppress_new_entries:
        # bootstrap 模式只建立 seen_ids，不回补历史文章到 issue。
        new_entries = []

    # 新一轮看到的条目始终排在最前面，便于后续优先保留最新的 seen_ids 窗口。
    merged_seen_ids = current_ids + [
        entry_id for entry_id in seen_ids if entry_id not in current_id_set
    ]
    return new_entries, merged_seen_ids[:max_seen_ids]
