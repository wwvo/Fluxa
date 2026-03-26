"""状态文件读写。"""

from __future__ import annotations

import json
from pathlib import Path

from fluxa.models import AppState, StateError


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"状态文件 JSON 解析失败: {path}") from exc

    if not isinstance(raw, dict):
        raise StateError("状态文件根节点必须是对象")

    return AppState.from_dict(raw)


def save_state(path: Path, state: AppState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        state.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    )
    path.write_text(f"{payload}\n", encoding="utf-8")
