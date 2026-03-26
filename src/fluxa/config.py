"""配置加载与校验。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from fluxa.models import AppConfig, ConfigError, FeedConfig, FeedDefaults


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败: {path}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError("配置文件根节点必须是对象")

    defaults_raw = _get_mapping(raw, "defaults", default={})
    defaults = FeedDefaults(
        timeout_seconds=_read_int(defaults_raw, "timeout_seconds", 20, minimum=1),
        max_entries_per_feed=_read_int(
            defaults_raw, "max_entries_per_feed", 20, minimum=1
        ),
        max_seen_ids=_read_int(defaults_raw, "max_seen_ids", 300, minimum=1),
        enabled=_read_bool(defaults_raw, "enabled", True),
    )

    feeds_raw = raw.get("feeds", [])
    if not isinstance(feeds_raw, list):
        raise ConfigError("feeds 必须是数组")

    feeds: list[FeedConfig] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(feeds_raw):
        if not isinstance(item, Mapping):
            raise ConfigError(f"feeds[{index}] 必须是对象")

        feed_id = _read_required_str(item, "id", prefix=f"feeds[{index}]")
        if feed_id in seen_ids:
            raise ConfigError(f"feed id 重复: {feed_id}")
        seen_ids.add(feed_id)
        feed_url = _read_required_str(item, "url", prefix=f"feeds[{index}]")

        feed = FeedConfig(
            id=feed_id,
            url=feed_url,
            fallback_urls=_read_optional_str_list(
                item,
                "fallback_urls",
                prefix=f"feeds[{index}]",
                exclude_values={feed_url},
            ),
            title=_read_optional_str(item, "title"),
            enabled=_read_bool(item, "enabled", defaults.enabled),
            timeout_seconds=_read_int(
                item,
                "timeout_seconds",
                defaults.timeout_seconds,
                minimum=1,
            ),
            max_entries_per_feed=_read_int(
                item,
                "max_entries_per_feed",
                defaults.max_entries_per_feed,
                minimum=1,
            ),
            max_seen_ids=_read_int(
                item,
                "max_seen_ids",
                defaults.max_seen_ids,
                minimum=1,
            ),
        )
        feeds.append(feed)

    return AppConfig(path=path, defaults=defaults, feeds=tuple(feeds))


def _get_mapping(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = payload.get(key, default)
    if not isinstance(value, Mapping):
        raise ConfigError(f"{key} 必须是对象")
    return value


def _read_required_str(payload: Mapping[str, Any], key: str, *, prefix: str) -> str:
    value = payload.get(key)
    if value is None:
        raise ConfigError(f"{prefix}.{key} 缺失")
    text = str(value).strip()
    if not text:
        raise ConfigError(f"{prefix}.{key} 不能为空")
    return text


def _read_optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_optional_str_list(
    payload: Mapping[str, Any],
    key: str,
    *,
    prefix: str,
    exclude_values: set[str] | None = None,
) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{prefix}.{key} 必须是字符串数组")

    excluded = exclude_values or set()
    deduped: list[str] = []
    seen: set[str] = set(excluded)
    for index, item in enumerate(value):
        text = str(item).strip()
        if not text:
            raise ConfigError(f"{prefix}.{key}[{index}] 不能为空")
        if text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return tuple(deduped)


def _read_bool(payload: Mapping[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} 必须是布尔值")
    return value


def _read_int(
    payload: Mapping[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int):
        raise ConfigError(f"{key} 必须是整数")
    if value < minimum:
        raise ConfigError(f"{key} 必须大于等于 {minimum}")
    return value
