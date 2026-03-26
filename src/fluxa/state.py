"""状态文件读写。

本模块只负责状态的序列化与反序列化，不参与抓取和发布决策。
这样 state 分支、JSON 结构和运行时对象之间的边界就保持在这一处。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fluxa.models import AppState, StateError


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"状态文件读取失败: {path}") from exc

    try:
        raw = json.loads(raw_text)
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
    try:
        _write_atomic_text(path, f"{payload}\n")
    except OSError as exc:
        raise StateError(f"状态文件写入失败: {path}") from exc


def _write_atomic_text(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.replace(path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
