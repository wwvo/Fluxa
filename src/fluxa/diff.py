"""基于 seen_ids 的增量计算。"""

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
        new_entries = []

    merged_seen_ids = current_ids + [
        entry_id for entry_id in seen_ids if entry_id not in current_id_set
    ]
    return new_entries, merged_seen_ids[:max_seen_ids]
