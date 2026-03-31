"""配置加载与校验。

本模块负责把仓库内的 `feeds/feeds.yml` 转为强类型配置对象。
它位于 Fluxa 执行链路的最前面，后续抓取、状态持久化和发布都只依赖这里产出的配置模型。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from fluxa.models import AppConfig, ConfigError, FeedConfig, FeedDefaults, _is_strict_int
from fluxa.rsshub import resolve_fallback_urls


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"配置文件读取失败: {path}") from exc

    try:
        raw = yaml.safe_load(raw_text)
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
        explicit_fallback_urls = _read_optional_str_list(
            item,
            "fallback_urls",
            prefix=f"feeds[{index}]",
        )

        feed = FeedConfig(
            id=feed_id,
            url=feed_url,
            # 非 RSSHub 源保持原样；RSSHub 源则在 Python 侧自动补出实例池回退顺序。
            fallback_urls=resolve_fallback_urls(
                feed_url,
                explicit_fallback_urls,
            ),
            title=_read_optional_str(item, "title", prefix=f"feeds[{index}]"),
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
    if not isinstance(value, str):
        raise ConfigError(f"{prefix}.{key} 必须是字符串")
    text = value.strip()
    if not text:
        raise ConfigError(f"{prefix}.{key} 不能为空")
    return text


def _read_optional_str(
    payload: Mapping[str, Any],
    key: str,
    *,
    prefix: str,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{prefix}.{key} 必须是字符串")
    text = value.strip()
    return text or None


def _read_optional_str_list(
    payload: Mapping[str, Any],
    key: str,
    *,
    prefix: str,
) -> tuple[str, ...]:
    value = payload.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{prefix}.{key} 必须是数组")

    normalized_values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"{prefix}.{key}[{index}] 必须是字符串")
        text = item.strip()
        if not text:
            raise ConfigError(f"{prefix}.{key}[{index}] 不能为空")
        normalized_values.append(text)
    return tuple(normalized_values)


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
    if not _is_strict_int(value):
        raise ConfigError(f"{key} 必须是整数")
    if value < minimum:
        raise ConfigError(f"{key} 必须大于等于 {minimum}")
    return value


