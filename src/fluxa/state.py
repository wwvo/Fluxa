"""状态文件读写。

本模块只负责状态的序列化与反序列化，不参与抓取和发布决策。
这样 state 分支、JSON 结构和运行时对象之间的边界就保持在这一处。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fluxa.models import AppState, PublishState, StateError


def load_state(path: Path) -> AppState:
    return AppState.from_dict(_load_json_object(path, "状态文件"))


def load_publish_state(path: Path) -> PublishState:
    return PublishState.from_dict(_load_json_object(path, "发布账本"))


def save_state(path: Path, state: AppState) -> None:
    _save_json_object(path, state.to_dict(), "状态文件")


def save_publish_state(path: Path, state: PublishState) -> None:
    _save_json_object(path, state.to_dict(), "发布账本")


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    if not path.exists():
        return {}

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"{label}读取失败: {path}") from exc

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise StateError(f"{label} JSON 解析失败: {path}") from exc

    if not isinstance(raw, dict):
        raise StateError(f"{label}根节点必须是对象")

    return raw


def _save_json_object(path: Path, payload: dict[str, object], label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    )
    try:
        _write_atomic_text(path, f"{content}\n")
    except OSError as exc:
        raise StateError(f"{label}写入失败: {path}") from exc


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
